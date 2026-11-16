"""resolve_supersession must use its `revision` argument, not just `doc_id`."""

from datetime import date
from types import SimpleNamespace

import pytest

from kanon.store import AuthorityStore, DocumentVersion, Edge
from kanon.tools import RevisionAuthority, make_tools

DATES_EVIDENCE = ("DATES: The effective date of the October 9, 1999 amendment to 10 CFR 50.59 "
                  "(64 FR 53613) is March 13, 2001.")


@pytest.fixture
def tool():
    s = AuthorityStore()
    s.upsert_document(DocumentVersion(
        doc_id="10CFR50.59", revision="1996-08-28", family="10CFR50.59", kind="regulation",
        status="superseded", effective_from=date(1996, 8, 28), effective_to=date(2001, 3, 13)))
    s.upsert_document(DocumentVersion(
        doc_id="10CFR50.59", revision="2001-03-13", family="10CFR50.59", kind="regulation",
        status="active", effective_from=date(2001, 3, 13), issue_date=date(1999, 10, 4)))
    s.add_edge(Edge(from_doc="10CFR50.59", from_rev="2001-03-13", to_doc="10CFR50.59",
                    to_rev="1996-08-28", edge_type="full", valid_from=date(2001, 3, 13),
                    evidence=DATES_EVIDENCE, confidence="explicit"))
    tools = make_tools(SimpleNamespace(), s, "graph")   # this tool never touches the index
    return {t.name: t for t in tools}["resolve_supersession"]


def call(tool, revision, as_of="2001-04-01"):
    return tool.invoke({"doc_id": "10CFR50.59", "revision": revision, "as_of": as_of})


def test_superseded_revision_does_not_govern_but_authority_names_the_one_that_did(tool):
    r = call(tool, "1996-08-28")
    assert isinstance(r, RevisionAuthority)
    assert r.governs_on_as_of is False
    assert r.authority.authoritative == [("10CFR50.59", "2001-03-13")]


def test_current_revision_governs(tool):
    assert call(tool, "2001-03-13").governs_on_as_of is True


def test_ended_by_names_the_edge_that_ended_the_revision(tool):
    [e] = call(tool, "1996-08-28").ended_by
    assert (e.from_rev, e.edge_type, e.valid_from) == ("2001-03-13", "full", date(2001, 3, 13))


def test_unknown_revision_does_not_raise(tool):
    r = call(tool, "9999-01-01")
    assert (r.governs_on_as_of, r.ended_by) == (False, [])


def test_result_survives_trace_serialisation(tool):
    from kanon.agent import _dump
    import json
    json.dumps(_dump(call(tool, "1996-08-28")), default=str)
