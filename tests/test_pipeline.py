"""Integration: real documents through the whole stack, offline.

Corpus: two real GovInfo CFR editions of 10 CFR 50.59 (2 pages each, already small):
  - CFR-1997 edition: pre-1999-amendment text, superseded on 2001-03-13
  - CFR-2002 edition: the amended text, effective 2001-03-13 (see tests/test_store.py for sources)
Embeddings: DeterministicFakeEmbedding (hash-based, no API) — these tests check plumbing and
authority logic, not semantic quality. LLM: a stub returning canned structured outputs.
"""

from datetime import date
from pathlib import Path

import chromadb
import pytest
from docling.chunking import HybridChunker
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage

from kanon import checks
from kanon.agent import Citation, Draft, QueryClass, build_agent, check_citations
from kanon.ingest import chunk_document, make_converter
from kanon.retrieval import CorpusIndex
from kanon.store import AuthorityStore, DocumentVersion, Edge
from kanon.trace import TraceRecorder

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "govinfo"
OLD = DocumentVersion(doc_id="10CFR50.59", revision="1997-01-01", family="10CFR50.59",
                      kind="regulation", status="superseded", effective_from=date(1997, 1, 1),
                      issue_date=date(1997, 1, 1))
NEW = DocumentVersion(doc_id="10CFR50.59", revision="2002-01-01", family="10CFR50.59",
                      kind="regulation", status="active", effective_from=date(2001, 3, 13),
                      issue_date=date(2002, 1, 1))
DIM = 64
TODAY = date(2026, 11, 14)

pytestmark = pytest.mark.skipif(not (RAW / "CFR-1997-title10-vol1-sec50-59.pdf").exists(),
                                reason="real CFR PDFs not downloaded (data/raw/govinfo)")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    store = AuthorityStore()
    for d in (OLD, NEW):
        store.upsert_document(d)
    store.add_edge(Edge(
        from_doc="10CFR50.59", from_rev="2002-01-01", to_doc="10CFR50.59", to_rev="1997-01-01",
        edge_type="full", valid_from=date(2001, 3, 13), confidence="explicit",
        evidence="DATES: The effective date of the October 9, 1999 amendment to 10 CFR 50.59 "
                 "(64 FR 53613) is March 13, 2001."))
    conv, chunker = make_converter(), HybridChunker()
    chunks = (chunk_document(RAW / "CFR-1997-title10-vol1-sec50-59.pdf", OLD, True, conv, chunker)
              + chunk_document(RAW / "CFR-2002-title10-vol1-sec50-59.pdf", NEW, True, conv, chunker))
    # PersistentClient on a temp dir, not EphemeralClient: ephemeral state is shared process-wide,
    # so another test's same-named collection (different dim) would collide with this one.
    client = chromadb.PersistentClient(path=str(tmp_path_factory.mktemp("chroma")))
    index = CorpusIndex(client, DeterministicFakeEmbedding(size=DIM), store)
    index.add(chunks)
    return store, index, chunks


def test_tier_a_green_on_real_documents(world):
    store, index, chunks = world
    checks.run_all(index, chunks, DIM)   # counts, dims, metadata, page, hot purity, round-trip


def test_hot_collection_has_zero_non_active_chunks(world):
    _, index, _ = world
    metas = index.hot.get(include=["metadatas"])["metadatas"]
    assert metas and all(m["status"] == "active" for m in metas)   # every row, not a sample


def test_dimension_mismatch_halts(world):
    _, index, chunks = world
    with pytest.raises(checks.PipelineIntegrityError, match="dim"):
        checks.check_index(index, {c.id for c in chunks}, DIM + 1)


def test_modes_differ_exactly_where_authority_differs(world):
    _, index, _ = world
    q = "changes, tests, and experiments safety evaluation"
    revs = lambda hits: {h.revision for h in hits}
    assert revs(index.search(q, 20, "none")) == {"1997-01-01", "2002-01-01"}
    assert revs(index.search(q, 20, "flag")) == {"2002-01-01"}
    # point-in-time: in mid-2000 the amended rule was issued but not yet effective
    assert revs(index.search(q, 20, "graph", date(2000, 6, 1))) == {"1997-01-01"}
    assert revs(index.search(q, 20, "graph", TODAY)) == {"2002-01-01"}


def test_gate_blocks_constructed_superseded_citation(world):
    store, _, chunks = world
    old_chunk = next(c for c in chunks if c.revision == "1997-01-01")
    cit = Citation(doc_id=old_chunk.doc_id, revision=old_chunk.revision, page=old_chunk.page)
    violations = check_citations(store, [cit], chunks, TODAY, "current")
    assert [v.rule for v in violations] == ["superseded"]
    assert "March 13, 2001" in violations[0].detail   # names the edge that ended it


class StubLLM:
    """Canned structured outputs. Cites whatever it's told to, like a model that trusts the
    retrieved text — the gate, not the model, must catch a bad citation."""

    def __init__(self, cite_revision: str, as_of: date | None = None):
        self.cite_revision = cite_revision
        self.as_of = as_of          # None = current-state question; else time-anchored

    def with_structured_output(self, schema):
        stub = self

        class Runner:
            def invoke(self, messages):
                if schema is QueryClass:
                    if stub.as_of:
                        return QueryClass(kind="time_anchored", as_of=stub.as_of)
                    return QueryClass(kind="current")
                return Draft(answer="Per 10 CFR 50.59 ...", citations=[
                    Citation(doc_id="10CFR50.59", revision=stub.cite_revision, page=1)])
        return Runner()

    def bind_tools(self, tools):
        stub = self

        class Runner:
            def invoke(self, messages):
                return AIMessage(content="", tool_calls=[{
                    "name": "search_current_requirements", "id": "1",
                    "args": {"query": "changes tests and experiments",
                             "as_of": (stub.as_of or TODAY).isoformat()}}])
        return Runner()


@pytest.mark.parametrize("cite", ["1997-01-01", "2002-01-01"])
def test_graph_mode_never_emits_superseded_citation_in_current_state(world, cite, tmp_path):
    store, index, _ = world
    rec = TraceRecorder()
    out = build_agent(StubLLM(cite), index, store, "graph").invoke(
        {"question": "What governs a 50.59 evaluation today?", "mode": "graph", "today": TODAY},
        config={"callbacks": [rec]})
    if out["decision"] == "answer":
        assert all(store.governs(c.doc_id, c.revision, TODAY) for c in out["citations"])
    if cite == "1997-01-01":
        # graph mode never retrieves the old edition, so citing it is both ungrounded and superseded
        assert out["decision"] == "abstain"
        assert {"superseded", "not_retrieved"} <= {v.rule for v in out["violations"]}
        assert "1997-01-01" in out["final"]                 # abstention names the revision
    else:
        assert out["decision"] == "answer"
    trace = rec.write(tmp_path, out)
    assert "Gate decision" in trace.read_text()


def test_baseline_mode_records_but_does_not_block(world):
    store, index, _ = world
    out = build_agent(StubLLM("1997-01-01"), index, store, "none").invoke(
        {"question": "What governs a 50.59 evaluation today?", "mode": "none", "today": TODAY})
    # In 'none' the old edition IS retrievable, so the citation is grounded — and wrong. The gate
    # records it (that's the superseded-citation metric) without blocking.
    assert out["decision"] == "answer"
    assert [v.rule for v in out["violations"]] == ["superseded"]


def test_graph_abstains_when_nothing_governed_on_the_date(world):
    # Earliest edition in this fixture is effective 1997-01-01; ask about April 1996 (the anchor
    # case's date). Graph retrieval finds nothing that governed -> abstain and say why, rather
    # than answer a requirements question from whatever else turned up.
    store, index, _ = world
    out = build_agent(StubLLM("1997-01-01", as_of=date(1996, 4, 1)), index, store, "graph").invoke(
        {"question": "What governed a pump replacement in April 1996?", "mode": "graph", "today": TODAY})
    assert out["decision"] == "abstain"
    assert "no_governing_requirement" in {v.rule for v in out["violations"]}


def test_requirements_search_is_added_when_model_omits_it(world):
    store, index, _ = world

    class OmitSearchLLM(StubLLM):
        def bind_tools(self, tools):
            class Runner:
                def invoke(self, messages):
                    return AIMessage(content="", tool_calls=[{
                        "name": "get_revision_history", "id": "history-only",
                        "args": {"doc_id": "10CFR50.59"}}])
            return Runner()

    out = build_agent(OmitSearchLLM("2002-01-01"), index, store, "graph").invoke(
        {"question": "What requirements govern a 50.59 evaluation?",
         "mode": "graph", "today": TODAY})
    assert any(c["name"] == "search_current_requirements" for c in out["tool_calls"])
    assert out["decision"] == "answer"
