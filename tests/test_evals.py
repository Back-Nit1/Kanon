"""Eval harness: metric arithmetic, the metric pairing, retrieval metrics on a tiny index, and an
offline end-to-end run with a stub model.

Fixture documents are placeholders ("DOC-A") making no claim about the world: these tests check
arithmetic and wiring, not NRC facts. Fake embeddings mean dense ranking is noise; with a
3-chunk corpus and k=5 every eligible chunk comes back, so assertions depend only on which
collections and authority filters each mode applies.
"""

import re
import tempfile
from datetime import date

import chromadb
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage

from kanon import evals
from kanon.agent import Citation, Draft, QueryClass
from kanon.ingest import Chunk
from kanon.judge import JudgeReport
from kanon.retrieval import CorpusIndex
from kanon.store import AuthorityStore, DocumentVersion, Edge

TODAY = date(2026, 1, 1)


def world():
    """DOC-A Rev 0 (2000) fully superseded by Rev 1 (2010); DOC-B Rev 0 (2015) active."""
    st = AuthorityStore()
    for doc, rev, status, eff in [("DOC-A", "0", "superseded", date(2000, 1, 1)),
                                  ("DOC-A", "1", "active", date(2010, 1, 1)),
                                  ("DOC-B", "0", "active", date(2015, 1, 1))]:
        st.upsert_document(DocumentVersion(doc_id=doc, revision=rev, kind="reg_guide",
                                           status=status, effective_from=eff))
    st.upsert_document(DocumentVersion(doc_id="EV-1", revision="0", kind="operating_experience",
                                       status="active", issue_date=date(1998, 1, 1)))
    st.add_edge(Edge(from_doc="DOC-A", from_rev="1", to_doc="DOC-A", to_rev="0", edge_type="full",
                     valid_from=date(2010, 1, 1), evidence="test fixture", confidence="explicit"))
    # A private on-disk client per call: EphemeralClient shares one in-memory system across the
    # whole pytest process, so collections (and their dimension) would leak between test files.
    idx = CorpusIndex(chromadb.PersistentClient(path=tempfile.mkdtemp()),
                      DeterministicFakeEmbedding(size=16), st)
    chunks = [_chunk("DOC-A", "0", "superseded", "2000-01-01", "pump replacement evaluation"),
              _chunk("DOC-A", "1", "active", "2010-01-01", "pump replacement evaluation revised"),
              _chunk("DOC-B", "0", "active", "2015-01-01", "pump seal guidance")]
    idx.add(chunks)
    return st, idx


def _chunk(doc, rev, status, eff, text):
    return Chunk(id=f"{doc}@{rev}#0", text=text, doc_id=doc, revision=rev, kind="reg_guide",
                 status=status, effective_from=eff, effective_to=None, issue_date="", section="s",
                 page=1, accession="", source_url="", paginated=True)


class Stub:
    """Offline stand-in for the chat model. Classifies by a year in the question, searches with
    the date the agent hands it, and cites the first source it is shown — so whatever it cites is
    decided by what each mode let through, which is the thing under test."""

    def __init__(self, judge_report: JudgeReport | None = None):
        self.judge_report = judge_report

    def with_structured_output(self, schema):
        stub = self

        class R:
            def invoke(self, msgs):
                text = msgs[-1].content
                if schema is QueryClass:
                    m = re.search(r"\b(19|20)\d\d\b", text)
                    return (QueryClass(kind="time_anchored", as_of=date(int(m[0]), 1, 1)) if m
                            else QueryClass(kind="current"))
                if schema is Draft:
                    m = re.search(r"doc_id=(\S+) \| revision=(\S+) \| page=(\d+)", text)
                    return Draft(answer="stub answer", citations=[
                        Citation(doc_id=m[1], revision=m[2], page=int(m[3]))])
                if schema is JudgeReport:
                    return stub.judge_report
                raise AssertionError(schema)
        return R()

    def bind_tools(self, tools):
        class R:
            def invoke(self, msgs):
                as_of = re.search(r"Authority date: (\d{4}-\d{2}-\d{2})", msgs[0].content)[1]
                return AIMessage(content="", tool_calls=[{"name": "search_current_requirements",
                                 "args": {"query": msgs[-1].content, "as_of": as_of}, "id": "1"}])
        return R()


def q(id, expected, as_of=None, category="time_anchored"):
    return evals.Question(id=id, category=category, query_kind="time_anchored" if as_of else "current",
                          as_of=as_of, question=f"pump replacement {id}", expected=expected)


# ---- the pairing --------------------------------------------------------------------------

def test_all_abstain_scores_zero_superseded_and_zero_answer_rate():
    rows = [{"decision": "abstain", "superseded": False, "cites_expected": False}] * 5
    s = evals.summarise_generation(rows)
    assert s["superseded_citation_rate"] == 0.0 and s["answer_rate"] == 0.0
    assert evals.pair(s) == "superseded-citation rate 0% @ answer rate 0% (0/5 answered)"


def test_superseded_rate_is_over_answered_questions_only():
    rows = ([{"decision": "answer", "superseded": True, "cites_expected": False}]
            + [{"decision": "answer", "superseded": False, "cites_expected": True}]
            + [{"decision": "abstain", "superseded": False, "cites_expected": False}] * 2)
    s = evals.summarise_generation(rows)
    assert (s["superseded_citation_rate"], s["answer_rate"], s["cites_expected_rate"]) == (0.5, 0.5, 0.5)


def test_generation_row_judges_superseded_by_store_and_pinned_date_not_the_model():
    st, _ = world()
    question = q("t", ["DOC-A@1"])
    # model resolved the wrong date (2005); scoring still uses the question's date (TODAY)
    state = {"decision": "answer", "as_of": date(2005, 1, 1), "violations": [],
             "citations": [Citation(doc_id="DOC-A", revision="0", page=1),
                           Citation(doc_id="EV-1", revision="0", page=1)]}
    row = evals.generation_row(question, state, st, TODAY)
    assert row["superseded"] and not row["cites_expected"] and row["as_of_mismatch"]
    # an event record alone is exempt from the governing check
    state["citations"] = [Citation(doc_id="EV-1", revision="0", page=1)]
    assert not evals.generation_row(question, state, st, TODAY)["superseded"]
    # an abstention never counts as a superseded citation, whatever it drafted
    state["decision"] = "abstain"
    state["citations"] = [Citation(doc_id="DOC-A", revision="0", page=1)]
    assert not evals.generation_row(question, state, st, TODAY)["superseded"]


# ---- retrieval metrics --------------------------------------------------------------------

def test_retrieval_row_recall_rr_and_non_governing():
    st, _ = world()
    chunks = [_chunk("DOC-B", "0", "active", "2015-01-01", "x"),
              _chunk("DOC-A", "1", "active", "2010-01-01", "x"),
              _chunk("DOC-A", "0", "superseded", "2000-01-01", "x")]
    row = evals.retrieval_row(q("t", ["DOC-A@1", "DOC-B@0"]), chunks, st, TODAY)
    assert (row["recall"], row["rr"], row["non_governing"]) == (1.0, 1.0, 1)
    row = evals.retrieval_row(q("t", ["DOC-A@1"]), chunks, st, TODAY)
    assert row["rr"] == 0.5
    assert evals.retrieval_row(q("t", []), chunks, st, TODAY)["recall"] is None
    # event records are never returned by search(), so they don't count against retrieval recall
    assert evals.retrieval_row(q("t", ["EV-1@0"]), chunks, st, TODAY)["recall"] is None
    assert evals.retrieval_row(q("t", ["DOC-A@1", "EV-1@0"]), chunks, st, TODAY)["recall"] == 1.0


def test_retrieval_eval_on_tiny_index_and_named_case():
    st, idx = world()
    questions = [q("past", ["DOC-A@0"], as_of=date(2005, 6, 1)), q("now", ["DOC-A@1", "DOC-B@0"])]
    res = {m: {"rows": evals.retrieval_eval(idx, st, TODAY, questions, m, k=5)} for m in evals.MODES}
    by = {m: {r["id"]: r for r in res[m]["rows"]} for m in evals.MODES}
    # 2005: flag only sees today's active set — misses Rev 0, returns two non-governing chunks
    assert by["flag"]["past"]["recall"] == 0.0 and by["flag"]["past"]["non_governing"] == 2
    assert by["graph"]["past"]["recall"] == 1.0 and by["graph"]["past"]["non_governing"] == 0
    assert by["none"]["past"]["non_governing"] == 2
    s = evals.summarise_retrieval(res["none"]["rows"])
    assert s["superseded_chunk_rate"] == 3 / 6   # pooled: 2 (past) + 1 (now: Rev 0) of 6
    cases = evals.failure_cases(res, None)
    assert [(c["id"], c["level"]) for c in cases] == [("past", "retrieval")]


def test_check_questions_rejects_underivable_expectations():
    st, _ = world()
    bad = [q("a", ["DOC-A@0"]),                              # superseded today
           q("b", ["NOPE@1"]),                               # not in store
           evals.Question(id="c", category="x", query_kind="time_anchored", question="?",
                          expected=[])]                      # time_anchored without as_of
    problems = evals.check_questions(st, TODAY, bad)
    assert len(problems) == 3
    assert evals.check_questions(st, TODAY, [q("ok", ["DOC-A@1"]), q("ev", ["EV-1@0"])]) == []


# ---- end to end, offline ------------------------------------------------------------------

def test_run_offline_end_to_end(tmp_path):
    st, idx = world()
    qfile = tmp_path / "questions.yaml"
    qfile.write_text(f"""today: {TODAY}
questions:
  - id: past
    category: effective_before_issue
    query_kind: time_anchored
    as_of: 2005-06-01
    question: "pump replacement evaluation in 2005"
    expected: ["DOC-A@0"]
""")
    res = evals.run(idx, st, qfile, k=5, llm=Stub(), trace_dir=tmp_path / "traces")
    gen = {m: res["generation"][m]["summary"] for m in evals.MODES}
    assert gen["graph"]["superseded_citation_rate"] == 0.0 and gen["graph"]["answer_rate"] == 1.0
    assert gen["flag"]["superseded_citation_rate"] == 1.0   # recorded, not enforced, in flag
    assert {(c["id"], c["level"]) for c in res["failure_cases"]} == {("past", "retrieval"),
                                                                      ("past", "generation")}
    report = evals.render_report(res)
    assert "effective date precedes issue date: **past** (generation)" in report
    # every superseded-citation figure in the report sits next to an answer rate
    for line in report.splitlines():
        if "superseded-citation rate" in line and "|" not in line and line.startswith("- `"):
            assert "answer rate" in line
    assert (tmp_path / "traces" / "graph" / "past").is_dir()


def test_report_says_so_when_no_thesis_case_exists(tmp_path):
    res = {"questions_file": "q", "questions_sha256": "0" * 64, "today": "2026-01-01", "k": 5,
           "n_questions": 1, "categories": {"partial": 1}, "generation": None,
           "retrieval": {}, "failure_cases": []}
    report = evals.render_report(res)
    assert "partial supersession: **no case found**" in report
    assert "cross-family supersession: **not tested**" in report
    assert "Not run" in report   # no generation -> no lone superseded-citation number
