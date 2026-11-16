"""The cross-encoder stage: it sees a deep pool, it sees only pre-filtered chunks, and its
order is the one that reaches generation. No model weights are downloaded here."""

import tempfile

import chromadb

from kanon.ingest import Chunk
from kanon.rerank import CrossEncoderReranker
from kanon.retrieval import RERANK_CANDIDATES, CorpusIndex
from kanon.store import AuthorityStore


class Embedder:
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def _chunk(doc_id, text, plants=("Plant A",), status="active"):
    return Chunk(id=doc_id, text=text, doc_id=doc_id, revision="1", kind="other",
                 status=status, effective_from="2020-01-01", effective_to=None,
                 issue_date="2020-01-01", section="body", page=1, accession="",
                 source_url="", paginated=True, applicability_scope="facility",
                 plants=list(plants), units=["1"])


def test_reranker_gets_a_deep_pre_filtered_pool_and_decides_the_final_k():
    seen = {}

    def fake_rerank(query, chunks):          # stands in for the cross-encoder
        seen["pool"] = [c.id for c in chunks]
        return sorted(chunks, key=lambda c: c.id, reverse=True)   # deterministic, not RRF's order

    # Own directory, not EphemeralClient: Chroma shares one in-process Ephemeral system, so a
    # second test's chunks would land in these same hot/cold collections.
    index = CorpusIndex(chromadb.PersistentClient(path=tempfile.mkdtemp()), Embedder(),
                        AuthorityStore(), reranker=fake_rerank)
    index.add([_chunk(f"doc{i:02d}", "pump replacement procedure") for i in range(12)]
              + [_chunk("wrong-plant", "pump replacement procedure", plants=["Plant B"]),
                 _chunk("superseded", "pump replacement procedure", status="superseded")])

    hits = index.search("pump replacement", 3, "flag", plant="Plant A")
    assert [c.id for c in hits] == ["doc11", "doc10", "doc09"]      # reranker's order, not fusion's
    # The pool is deep (fusion became a recall stage) but still only eligible chunks: the
    # cross-encoder never pays for a wrong-plant or non-active chunk.
    assert len(seen["pool"]) == 12 and len(seen["pool"]) <= RERANK_CANDIDATES
    assert {"wrong-plant", "superseded"}.isdisjoint(seen["pool"])

    index.reranker = None
    assert len(index.search("pump replacement", 3, "flag", plant="Plant A")) == 3


def test_cross_encoder_sorts_by_score_and_leaves_one_candidate_alone():
    class Stub(CrossEncoderReranker):
        def scores(self, query, texts):      # no weights, no torch
            return [float(len(t)) for t in texts]

    r = Stub()
    chunks = [_chunk("short", "a"), _chunk("longest", "aaa"), _chunk("mid", "aa")]
    ranked = r("query", chunks)
    assert [c.id for c in ranked] == ["longest", "mid", "short"]
    assert [c.score for c in ranked] == [3.0, 2.0, 1.0]
    assert r("query", chunks[:1])[0].score is None   # single candidate: nothing to reorder
