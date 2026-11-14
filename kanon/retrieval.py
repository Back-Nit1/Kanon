"""Layer 2 — hybrid retrieval over hot/cold collections, in three authority modes.

Hot/cold instead of one collection + a status filter:
  A `where={"status": "active"}` filter is one refactor away from deletion and nothing visibly
  breaks — you just quietly start citing dead documents. With two collections, superseded text
  is unreachable from the default code path. Superseded chunks are *tiered*, never evicted:
  cold is searched for time-anchored queries and operating-experience lookups.
  Bonus: superseded revisions are near-duplicates of current ones — the worst possible ranking
  distractors — so keeping them out of the default index improves ranking, not just speed.

Vector store: Chroma — local persistence, metadata filters, named collections; all we need.
  Rejected Qdrant: better filtered search at scale, but this corpus is thousands of chunks and
  the extra operational surface buys nothing measurable.

Fusion: reciprocal rank fusion of BM25 and dense, written out below (6 lines) rather than
  LangChain's EnsembleRetriever, which moved to `langchain-classic` in LangChain 1.0. RRF is
  easy to say out loud: each list votes 1/(60 + rank) for each doc; sum the votes.
  BM25 matters here because regulatory text is full of exact identifiers ("50.59", "RG 1.187",
  "MP-033.05") that dense embeddings blur together.

Reranking: deliberately omitted in v1. A reranker scores topical relevance, and a superseded
  revision is topically near-identical to the current one — orthogonal to the thesis. If one is
  added (BAAI/bge-reranker-v2-m3), it must also be tested in mode 'none': the hypothesis is that
  better relevance ranking *raises* the superseded-citation rate.

Modes (the central comparison):
  none  — hot + cold, no authority filtering. Baseline.
  flag  — hot only, i.e. the boolean status='active' filter. The "obvious fix". It knows only
          what is current *today*: it cannot answer as_of=1996, and it can't see partial or
          cross-family supersession.
  graph — hot + cold, pre-filtered to versions the bi-temporal resolver says governed on as_of.
"""

import re
from datetime import date
from typing import Literal

import chromadb
from langchain_core.embeddings import Embeddings
from rank_bm25 import BM25Okapi

from kanon.ingest import Chunk
from kanon.store import AuthorityStore

Mode = Literal["none", "flag", "graph"]
# Requirements state what governs (CFR sections, guides, tech specs, amendments). Records state
# what happened on a date: an event (inspection/enforcement/LER), a rule change (Federal Register
# final rule), a withdrawal notice. Records never "govern", so they're exempt from the authority
# check and searched only by find_precedent_events. The CFR is the codified law; the Federal
# Register is its change log.
# Explicit list on purpose: an unknown kind defaults to *requirement*, i.e. subject to the gate.
RECORD_KINDS = ("operating_experience", "rulemaking", "notice")


def is_record(kind: str) -> bool:
    return kind in RECORD_KINDS


RRF_K = 60          # standard constant from Cormack et al. 2009; dampens the top ranks' dominance
CANDIDATES = 50     # per-retriever depth before fusion

# Keep dotted identifiers whole: "50.59", "1.187", "033.05". Default \w+ would split them into
# meaningless digit fragments and BM25 would lose its main advantage on this corpus.
TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class CorpusIndex:
    def __init__(self, client: chromadb.ClientAPI, embedder: Embeddings, store: AuthorityStore):
        self.embedder = embedder
        self.store = store
        cosine = {"hnsw": {"space": "cosine"}}
        self.hot = client.get_or_create_collection("kanon_hot", configuration=cosine)
        self.cold = client.get_or_create_collection("kanon_cold", configuration=cosine)
        self._bm25: dict[str, tuple[dict, BM25Okapi]] = {}

    # ---- writes ---------------------------------------------------------------------------

    def add(self, chunks: list[Chunk], batch: int = 128) -> None:
        """Route each chunk by its parent's status at ingest: active -> hot, else -> cold."""
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            vectors = self.embedder.embed_documents([c.text for c in part])
            for col, active in ((self.hot, True), (self.cold, False)):
                sel = [(c, v) for c, v in zip(part, vectors) if (c.status == "active") == active]
                if sel:
                    col.upsert(ids=[c.id for c, _ in sel], embeddings=[v for _, v in sel],
                               documents=[c.text for c, _ in sel],
                               metadatas=[c.chroma_metadata() for c, _ in sel])
        self._bm25.clear()
        # ponytail: no re-tiering when a document's status changes after ingest; re-ingest moves
        # it. Add a retier() (copy hot->cold with stored embeddings, then remove from hot) when
        # incremental status updates are needed. Never a bare delete.

    # ---- reads ----------------------------------------------------------------------------

    def search(self, query: str, k: int, mode: Mode, as_of: date | None = None) -> list[Chunk]:
        """Requirement documents only (records are excluded; see search_precedent)."""
        if mode == "flag":
            return self._hybrid(query, k, [self.hot], allowed=None, precedent=False)
        if mode == "none":
            return self._hybrid(query, k, [self.hot, self.cold], allowed=None, precedent=False)
        if as_of is None:
            raise ValueError("mode 'graph' requires as_of")   # the date is the whole point
        allowed = {f"{d}@{r}" for d, r in self.store.governing_set(as_of)}
        return self._hybrid(query, k, [self.hot, self.cold], allowed=allowed, precedent=False)

    def search_precedent(self, query: str, k: int, since: date | None = None) -> list[Chunk]:
        """Records across hot AND cold. See tools.find_precedent_events for why superseded
        status doesn't disqualify a record."""
        hits = self._hybrid(query, k * 3, [self.hot, self.cold], allowed=None, precedent=True)
        if since:
            hits = [c for c in hits if (c.issue_date or c.effective_from) >= since.isoformat()]
        return hits[:k]

    def _hybrid(self, query: str, k: int, cols: list, allowed: set[str] | None,
                precedent: bool) -> list[Chunk]:
        if allowed is not None and not allowed:
            return []
        where = _where(allowed, precedent)
        qvec = self.embedder.embed_query(query)
        rankings, found = [], {}
        for col in cols:
            if col.count() == 0:
                continue
            dense = col.query(query_embeddings=[qvec], n_results=min(CANDIDATES, col.count()),
                              where=where, include=["documents", "metadatas"])
            ids = dense["ids"][0]
            rankings.append(ids)
            found.update({i: (t, m) for i, t, m in zip(ids, dense["documents"][0], dense["metadatas"][0])})
            rankings.append(self._bm25_rank(col, query, allowed, precedent, found))
        fused = sorted(rrf(rankings).items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [Chunk.from_chroma(i, *found[i], score=s) for i, s in fused]

    def _bm25_rank(self, col, query: str, allowed: set[str] | None, precedent: bool,
                   found: dict) -> list[str]:
        # ponytail: BM25 index rebuilt in memory from Chroma per process. Fine for thousands of
        # chunks; persist it (or use a search engine) at ~10^6.
        if col.name not in self._bm25:
            data = col.get(include=["documents", "metadatas"])
            self._bm25[col.name] = (data, BM25Okapi([tokenize(t) for t in data["documents"]]))
        data, bm25 = self._bm25[col.name]
        scores = bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order:
            meta = data["metadatas"][i]
            if scores[i] <= 0 or len(out) >= CANDIDATES:
                break
            # same predicate as _where(), applied in Python because BM25 lives outside Chroma
            if (allowed is None or meta["doc_key"] in allowed) and is_record(meta["kind"]) == precedent:
                out.append(data["ids"][i])
                found[data["ids"][i]] = (data["documents"][i], meta)
        return out


def _where(allowed: set[str] | None, precedent: bool) -> dict:
    kind = {"kind": {"$in" if precedent else "$nin": list(RECORD_KINDS)}}
    if allowed is None:
        return kind
    return {"$and": [kind, {"doc_key": {"$in": sorted(allowed)}}]}
