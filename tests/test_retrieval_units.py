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
