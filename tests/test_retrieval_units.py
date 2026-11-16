from kanon.retrieval import rrf, tokenize
from kanon.retrieval import CorpusIndex
from kanon.ingest import Chunk
from kanon.store import AuthorityStore
import chromadb


def test_tokenizer_keeps_regulatory_identifiers_whole():
    toks = tokenize("Per 10 CFR 50.59 and RG 1.187, see MP-033.05 Rev 28.")
    assert {"50.59", "1.187", "mp-033.05"} <= set(toks)


def test_rrf_rewards_agreement_between_retrievers():
    # 'b' is 2nd in both lists; 'a' is 1st in one and absent from the other.
    scores = rrf([["a", "b"], ["c", "b"]])
    assert scores["b"] > scores["a"] and scores["b"] > scores["c"]
    assert abs(scores["a"] - 1 / 61) < 1e-12


def test_filtered_hybrid_isolation_and_exact_identifiers():
    class Embedder:
        def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]
        def embed_query(self, text):
            return [1.0, 0.0]

    index = CorpusIndex(chromadb.EphemeralClient(), Embedder(), AuthorityStore())
    def chunk(doc_id, text, scope="facility", plants=None, units=None, kind="other", status="active"):
        return Chunk(id=doc_id, text=text, doc_id=doc_id, revision="1", kind=kind,
                     status=status, effective_from="2020-01-01", effective_to=None,
                     issue_date="2020-01-01", section="body", page=1, accession="",
                     source_url="", paginated=True, applicability_scope=scope,
                     plants=plants or [], units=units or [])
    index.add([
        chunk("target", "Document QP 1104 pump replacement", plants=["Plant A"], units=["1"]),
        chunk("wrong", "Document QP 1104 pump replacement", plants=["Plant B"], units=["1"]),
        chunk("unknown", "Document QP 1104 pump replacement", scope="unknown"),
        chunk("global", "10 CFR 50.59 pump replacement", scope="global"),
        chunk("old", "Document QP 1104 pump replacement", plants=["Plant A"], units=["1"], status="superseded"),
        chunk("event", "pump replacement incident", plants=["Plant A"], units=["1"], kind="operating_experience", status="superseded"),
    ])
    hits = index.search("QP 1104", 10, "flag", plant="plant a", unit="1")
    assert {c.id for c in hits} == {"target", "global"}
    assert hits[0].id == "target"
    assert index.search("10 CFR 50.59", 1, "flag", plant="Plant A")[0].id == "global"
    assert {c.id for c in index.search_precedent("pump replacement", 10, plant="Plant A")} == {"event"}
    assert {c.id for c in index.search("pump replacement", 10, "flag", plant="Plant A", unit="9")} == {"global"}


class _FakeCol:
    def __init__(self, chunks):
        self.rows = chunks

    def get(self, where, include):
        rows = [c for c in self.rows if c.doc_key == where["doc_key"]]
        return {"ids": [c.id for c in rows], "documents": [c.text for c in rows],
                "metadatas": [c.chroma_metadata() for c in rows]}


def _fake_index(hot, cold):
    index = object.__new__(CorpusIndex)   # no embedder, store or Chroma: chunks_for needs none
    index.hot, index.cold = _FakeCol(hot), _FakeCol(cold)
    return index


def _page_chunk(id, page):
    return Chunk(id=id, text=f"text {page}", doc_id="10CFR50.59", revision="1996-08-28",
                 kind="regulation", status="superseded", effective_from="1996-08-28",
                 effective_to=None, issue_date="1996-08-28", section="body", page=page,
                 accession="", source_url="", paginated=True)


def test_chunks_for_merges_tiers_in_page_order():
    index = _fake_index(hot=[_page_chunk("b", 2)], cold=[_page_chunk("a", 1)])
    got = index.chunks_for("10CFR50.59@1996-08-28")
    assert [c.page for c in got] == [1, 2]
    assert got[0].score is None and got[0].effective_to is None


def test_chunks_for_unknown_doc_key_is_empty():
    assert _fake_index(hot=[], cold=[]).chunks_for("RG-1.187@2") == []
