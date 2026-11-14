"""Resolver tests on a real timeline: the 1999 revision of 10 CFR 50.59.

Facts (read from the fetched Federal Register PDFs in data/raw/fr/):
  - FR-1999-10-04_99-25054.pdf: 64 FR 53582, Monday, October 4, 1999 — final rule amending 50.59;
    "the final rule becomes effective in 90 days" after a later notice announcing guidance.
  - FR-2000-12-13_00-31735.pdf: Wednesday, December 13, 2000 — "DATES: The effective date of the
    October 9, 1999 amendment to 10 CFR 50.59 (64 FR 53613) is March 13, 2001."
  - data/raw/govinfo/CFR-1997-title10-vol1-sec50-59.pdf: the pre-amendment text as of the
    January 1, 1997 CFR edition (the earliest date this repo can attest it governed).

So the new text was *issued* 17 months before it *governed*. Anything ranking by issue date
would apply it from October 1999. That is the bi-temporal case.
"""

from datetime import date, datetime, timezone

import pytest

from kanon.store import AuthorityStore, DocumentVersion, Edge

DATES_EVIDENCE = ("DATES: The effective date of the October 9, 1999 amendment to 10 CFR 50.59 "
                  "(64 FR 53613) is March 13, 2001.")


@pytest.fixture
def store() -> AuthorityStore:
    s = AuthorityStore()
    s.upsert_document(DocumentVersion(
        doc_id="10CFR50.59", revision="1997-01-01", family="10CFR50.59", kind="regulation",
        status="superseded", effective_from=date(1997, 1, 1), issue_date=date(1997, 1, 1)))
    s.upsert_document(DocumentVersion(
        doc_id="10CFR50.59", revision="1999-10-04", family="10CFR50.59", kind="regulation",
        status="active", effective_from=date(2001, 3, 13), issue_date=date(1999, 10, 4)))
    s.add_edge(Edge(from_doc="10CFR50.59", from_rev="1999-10-04", to_doc="10CFR50.59",
                    to_rev="1997-01-01", edge_type="full", valid_from=date(2001, 3, 13),
                    evidence=DATES_EVIDENCE, confidence="explicit"))
    return s


def test_anchor_date_1996_has_no_attested_text(store):
    # The anchor case (April 1996) predates the earliest edition we hold. The resolver must say
    # "nothing" rather than reach for the nearest edition — that gap is real and must be filled
    # with an older GovInfo edition, not papered over.
    assert store.resolve_authority("10CFR50.59", date(1996, 4, 1)).authoritative == []


def test_old_text_governs_after_new_rule_is_issued_but_before_it_is_effective(store):
    # Bi-temporal: issue_date (1999-10-04) is later than the old edition's, but effective_from
    # (2001-03-13) is what decides. Recency-by-issue-date would wrongly pick 1999-10-04 here.
    res = store.resolve_authority("10CFR50.59", date(2000, 6, 1))
    assert res.authoritative == [("10CFR50.59", "1997-01-01")]
    assert res.chain == []   # the supersession edge isn't in effect yet


def test_new_text_governs_from_its_effective_date(store):
    res = store.resolve_authority("10CFR50.59", date(2001, 3, 13))
    assert res.authoritative == [("10CFR50.59", "1999-10-04")]
    assert [e.evidence for e in res.chain] == [DATES_EVIDENCE]
    assert res.resolved


def test_revision_history_orders_by_effective_not_issue_date(store):
    assert [d.revision for d in store.revision_history("10CFR50.59")] == ["1999-10-04", "1997-01-01"]


def test_record_time_replay_ignores_edges_not_yet_ingested(store):
    # Record-time axis: before we ingested the edge, the store could not know the old text ended.
    before = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert store.governs("10CFR50.59", "1997-01-01", date(2002, 1, 1), known_at=before)
    assert not store.governs("10CFR50.59", "1997-01-01", date(2002, 1, 1))


def test_invalidated_edge_is_retracted_but_kept(store):
    edge = store.edges_into("10CFR50.59", "1997-01-01")[0]
    store.invalidate_edge(edge)
    assert store.edges_into("10CFR50.59", "1997-01-01") == []          # current belief
    rows = store.db.execute("SELECT COUNT(*) FROM supersession_edges").fetchone()[0]
    assert rows == 1                                                    # never deleted


def test_undated_duplicate_of_a_dated_edge_is_corroboration_not_ambiguity(store):
    # Same supersession stated twice, once without a date: no new ambiguity, nothing blocks.
    store.add_edge(Edge(from_doc="10CFR50.59", from_rev="1999-10-04", to_doc="10CFR50.59",
                        to_rev="1997-01-01", edge_type="full", valid_from=None,
                        evidence="(same rule, effective date not stated)", confidence="unresolved"))
    assert store.resolve_authority("10CFR50.59", date(2005, 1, 1)).resolved
    assert store.unresolved_into("10CFR50.59", "1997-01-01") == []
