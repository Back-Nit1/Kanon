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

import math
import re
from datetime import date
from typing import Literal
from urllib.parse import urlparse

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


def open_chroma(s) -> chromadb.ClientAPI:
    """Local PersistentClient is single-process (SQLite underneath). Parallel ingest workers
    need the Chroma server: set KANON_CHROMA_URL (docker-compose.yml runs one)."""
    if not s.chroma_url:
        return chromadb.PersistentClient(path=str(s.chroma_dir))
    u = urlparse(s.chroma_url)
    return chromadb.HttpClient(host=u.hostname, port=u.port or 8000, ssl=u.scheme == "https")


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
    def __init__(self, client: chromadb.ClientAPI, embedder: Embeddings, store: AuthorityStore,
                 expected_dim: int | None = None):
        self.embedder = embedder
        self.store = store
        self.expected_dim = expected_dim
        cosine = {"hnsw": {"space": "cosine"}}
        self.hot = client.get_or_create_collection("kanon_hot", configuration=cosine)
        self.cold = client.get_or_create_collection("kanon_cold", configuration=cosine)
        self._bm25: dict[str, tuple[dict, BM25Okapi]] = {}

    # ---- writes ---------------------------------------------------------------------------

    def embed(self, chunks: list[Chunk]) -> list[list[float]]:
        """Embed without publishing. Queue workers validate this result before any upsert."""
        return self.embedder.embed_documents([c.text for c in chunks])

    def validate_publish(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Tier-A checks that must pass before a batch becomes searchable."""
        if not chunks or len(vectors) != len(chunks):
            raise ValueError(f"embedding count {len(vectors)} != chunk count {len(chunks)}")
        dims = {len(v) for v in vectors}
        if not dims or 0 in dims or len(dims) != 1:
            raise ValueError(f"embedding dimensions are empty or inconsistent: {sorted(dims)}")
        dim = next(iter(dims))
        if self.expected_dim is not None and dim != self.expected_dim:
            raise ValueError(f"embedding dim {dim} != configured {self.expected_dim}")
        if any(not math.isfinite(float(value)) for vector in vectors for value in vector):
            raise ValueError("embedding contains a non-finite value")

        required = (set(Chunk.model_fields) - {"id", "text", "score"}) | {"doc_key"}
        for chunk in chunks:
            meta = chunk.chroma_metadata()
            missing = required - set(meta)
            if missing:
                raise ValueError(f"{chunk.id}: stored without metadata {sorted(missing)}")
            if not isinstance(meta.get("page"), int) or meta["page"] < 1:
                raise ValueError(f"{chunk.id}: stored without a valid page")
            if chunk.status not in {"active", "superseded", "withdrawn"}:
                raise ValueError(f"{chunk.id}: invalid authority status {chunk.status!r}")

    def write(self, chunks: list[Chunk], vectors: list[list[float]],
              ingest_key: str | None = None) -> None:
        """Validate, then route active -> hot and every non-active revision -> cold."""
        self.validate_publish(chunks, vectors)
        for col, active in ((self.hot, True), (self.cold, False)):
            sel = [(c, v) for c, v in zip(chunks, vectors) if (c.status == "active") == active]
            if sel:
                metadatas = [c.chroma_metadata() for c, _ in sel]
                if ingest_key:
                    for metadata in metadatas:
                        metadata["_ingest_key"] = ingest_key
                col.upsert(ids=[c.id for c, _ in sel], embeddings=[v for _, v in sel],
                           documents=[c.text for c, _ in sel], metadatas=metadatas)
        self._bm25.clear()

    def add(self, chunks: list[Chunk], batch: int = 128) -> None:
        """Compatibility path: embed, verify and publish each batch."""
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            vectors = self.embed(part)
            self.write(part, vectors)

    def remove_doc(self, doc_key: str) -> None:
        """Drop one revision's chunks from both tiers: before re-ingesting a changed file (so a
        shorter new version leaves no stale tail) and when its ingest job dies half-written."""
        for col in (self.hot, self.cold):
            col.delete(where={"doc_key": doc_key})
        self._bm25.clear()
        # ponytail: no re-tiering when a document's status changes after ingest; re-ingest moves
        # it. Add a retier() (copy hot->cold with stored embeddings, then remove from hot) when
        # incremental status updates are needed. Never a bare delete.

    def remove_ingest(self, ingest_key: str) -> None:
        """Remove only vectors still owned by one failed identity, never a newer overwrite."""
        for col in (self.hot, self.cold):
            col.delete(where={"_ingest_key": ingest_key})
        self._bm25.clear()

    # ---- reads ----------------------------------------------------------------------------

    def search(self, query: str, k: int, mode: Mode, as_of: date | None = None,
               plant: str | None = None, unit: str | None = None,
               docket: str | None = None) -> list[Chunk]:
        """Requirement documents only (records are excluded; see search_precedent)."""
        if mode == "flag":
            return self._hybrid(query, k, [self.hot], allowed=None, precedent=False, filters=(plant, unit, docket))
        if mode == "none":
            return self._hybrid(query, k, [self.hot, self.cold], allowed=None, precedent=False, filters=(plant, unit, docket))
        if as_of is None:
            raise ValueError("mode 'graph' requires as_of")   # the date is the whole point
        allowed = {f"{d}@{r}" for d, r in self.store.governing_set(as_of)}
        return self._hybrid(query, k, [self.hot, self.cold], allowed=allowed, precedent=False, filters=(plant, unit, docket))

    def search_precedent(self, query: str, k: int, since: date | None = None,
                         plant: str | None = None, unit: str | None = None,
                         docket: str | None = None) -> list[Chunk]:
        """Records across hot AND cold. See tools.find_precedent_events for why superseded
        status doesn't disqualify a record."""
        hits = self._hybrid(query, k * 3, [self.hot, self.cold], allowed=None, precedent=True,
                            filters=(plant, unit, docket), since=since)
        if since:
            hits = [c for c in hits if (c.issue_date or c.effective_from) >= since.isoformat()]
        return hits[:k]

    def _hybrid(self, query: str, k: int, cols: list, allowed: set[str] | None,
                precedent: bool, filters: tuple = (None, None, None),
                since: date | None = None) -> list[Chunk]:
        if allowed is not None and not allowed:
            return []
        where = _where(allowed, precedent)
        qvec = self.embedder.embed_query(query)
        rankings, found = [], {}
        for col in cols:
            if col.count() == 0:
                continue
            # Filter the candidate universe before either ranking, so unrelated chunks cannot
            # consume the fixed candidate budget. JSON strings work on old and new Chroma.
            data = col.get(where=where, include=["documents", "metadatas"])
            eligible = {i for i, m in zip(data["ids"], data["metadatas"])
                        if _matches(m, filters) and (since is None or
                           (m.get("issue_date") or m.get("effective_from") or "") >= since.isoformat())}
            if not eligible:
                continue
            dense = col.query(query_embeddings=[qvec], n_results=min(CANDIDATES, col.count()),
                              ids=sorted(eligible), where=where, include=["documents", "metadatas"])
            ids = dense["ids"][0]
            rankings.append(ids)
            found.update({i: (t, m) for i, t, m in zip(ids, dense["documents"][0], dense["metadatas"][0])})
            rankings.append(self._bm25_rank(col, query, allowed, precedent, found, eligible))
        fused = sorted(rrf(rankings).items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [Chunk.from_chroma(i, *found[i], score=s) for i, s in fused]

    def _bm25_rank(self, col, query: str, allowed: set[str] | None, precedent: bool,
                   found: dict, eligible: set[str]) -> list[str]:
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
            if data["ids"][i] in eligible:
                out.append(data["ids"][i])
                found[data["ids"][i]] = (data["documents"][i], meta)
        return out


def _where(allowed: set[str] | None, precedent: bool) -> dict:
    kind = {"kind": {"$in" if precedent else "$nin": list(RECORD_KINDS)}}
    if allowed is None:
        return kind
    return {"$and": [kind, {"doc_key": {"$in": sorted(allowed)}}]}


def _matches(meta: dict, filters: tuple) -> bool:
    if not any(filters):
        return True
    if meta.get("applicability_scope") == "global":
        return True
    if meta.get("applicability_scope") != "facility":
        return False
    import json
    for key, requested in zip(("plants", "units", "dockets"), filters):
        if requested:
            values = meta.get(key, [])
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except (ValueError, TypeError):
                    return False
            normalize = lambda s: " ".join(str(s).casefold().split())
            if normalize(requested) not in {normalize(v) for v in values}:
                return False
    return True
