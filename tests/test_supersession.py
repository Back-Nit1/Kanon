"""Parser + resolver on verbatim NRC strings from data/labels/supersession_evidence.jsonl and
data/labels/rg_index.jsonl. Every quoted string below was copied from those files, which were
checked against the source documents (FR notices on federalregister.gov/govinfo, NRC RG index).
"""

from datetime import date

import pytest

from kanon.store import AuthorityStore, DocumentVersion
from kanon.supersession import load_labels, parse_evidence, resolve_wildcards

# 80 FR 24293 (RG 1.1 withdrawal notice)
RG11_WITHDRAW = "DATES: Effective April 30, 2015, the NRC withdraws RG 1.1."
RG11_INCORP = ("The NRC is withdrawing RG 1.1 because its guidance has been incorporated into "
               "RG 1.82, ‘‘Water Sources for Long-Term Recirculation Cooling Following a "
               "Loss-of-Coolant Accident.’’")
# NRC RG index history for RG 1.85
RG185_INDEX = ("06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 "
               "of RG 1.84")
RG182_REDUNDANT = ("The document is redundant due to the inclusion of its subject matter in Rev. 3 "
                   "of RG 1.160, ‘‘Monitoring the Effectiveness of Maintenance at Nuclear Power "
                   "Plants.’’")
RG95_INDEX_W = ("Protection of Nuclear Power Plant Control Room Operators Against an Accidental "
                "Chlorine Release(Withdrawn -- See 67 FR 939 01/08/2002)")
# 72 FR 49352 (2007 amendment of 10 CFR 50.59)
CFR_2007 = "In § 50.59, paragraphs (b), (d)(2), and (d)(3) are revised to read as follows:"
# 65 FR 77773 — an effective-date statement, not a supersession statement
CFR_DATES = ("The effective date of the October 9, 1999 amendment to 10 CFR 50.59 (64 FR 53613) "
             "is March 13, 2001.")
# 85 FR 40696
RG187_EFFECT = "Revision 2 to RG 1.187 takes effect on July 7, 2020."


def test_withdrawal_with_exact_date_is_explicit():
    [e] = parse_evidence("RG-1.1", "0", RG11_WITHDRAW)
    assert (e.edge_type, e.valid_from, e.confidence) == ("withdrawn", date(2015, 4, 30), "explicit")


def test_mdy_date_in_index_withdrawal():
    [e] = parse_evidence("RG-1.95", "1", RG95_INDEX_W)
    assert (e.edge_type, e.valid_from) == ("withdrawn", date(2002, 1, 8))


def test_incorporated_into_other_guide_is_cross_family_and_undated_means_unresolved():
    [e] = parse_evidence("RG-1.1", "0", RG11_INCORP)
    assert (e.from_doc, e.from_rev, e.to_doc, e.to_rev) == ("RG-1.82", "*", "RG-1.1", "0")
    assert (e.edge_type, e.confidence) == ("cross_family", "unresolved")   # no date in the text


def test_revision_first_reference_and_month_only_date_is_inferred():
    [e] = parse_evidence("RG-1.85", "31", RG185_INDEX)
    assert (e.from_doc, e.from_rev, e.to_doc, e.to_rev) == ("RG-1.84", "32", "RG-1.85", "31")
    assert (e.valid_from, e.confidence) == (date(2003, 6, 1), "inferred")   # "06/2003": day assumed


def test_subject_matter_inclusion_phrase():
    [e] = parse_evidence("RG-1.182", "0", RG182_REDUNDANT, stated_date=date(2012, 11, 27))
    assert (e.from_doc, e.from_rev, e.edge_type) == ("RG-1.160", "3", "cross_family")


def test_cfr_amendment_instruction_replaces_prior_compiled_version():
    [e] = parse_evidence("10CFR50.59", "2007-09-27", CFR_2007, stated_date=date(2007, 9, 27))
    assert (e.edge_type, e.to_rev) == ("full", "*")      # prior version pinned later
    assert "(d)(2)" in e.scope_note


def test_effective_date_statement_is_not_a_supersession():
    assert parse_evidence("10CFR50.59", "2001-03-13", CFR_DATES) == []


def test_wildcard_pins_old_side_to_version_in_effect_before_the_change():
    s = AuthorityStore()
    for rev, eff in (("2001-12-14", date(2001, 12, 14)), ("2007-09-27", date(2007, 9, 27))):
        s.upsert_document(DocumentVersion(doc_id="10CFR50.59", revision=rev, kind="regulation",
                                          status="active", effective_from=eff))
    [e] = parse_evidence("10CFR50.59", "2007-09-27", CFR_2007, stated_date=date(2007, 9, 27))
    pinned = resolve_wildcards(e, s)
    assert (pinned.to_rev, pinned.confidence) == ("2001-12-14", "inferred")


@pytest.fixture
def rg187(tmp_path):
    """RG 1.187: index dates Rev 2 06/2020; 85 FR 40696 says it takes effect July 7, 2020."""
    s = AuthorityStore()
    for rev, month, status in (("1", "2019-05", "superseded"), ("2", "2020-06", "superseded"),
                               ("3", "2021-06", "active")):
        d = date.fromisoformat(month + "-01")
        s.upsert_document(DocumentVersion(doc_id="RG-1.187", revision=rev, kind="reg_guide",
                                          status=status, effective_from=d, issue_date=d))
    f = tmp_path / "ev.jsonl"
    f.write_text('{"subject_doc": "RG-1.187", "subject_rev": "2", "evidence": "%s", '
                 '"stated_date": "2020-07-07"}\n' % RG187_EFFECT)
    load_labels(s, f)
    return s


def test_effective_date_differs_from_issue_date_and_wins(rg187):
    # Mid-June 2020: Rev 2 was issued (06/2020) but did not take effect until July 7.
    assert rg187.resolve_authority("RG-1.187", date(2020, 6, 15)).authoritative == [("RG-1.187", "1")]
    assert rg187.resolve_authority("RG-1.187", date(2020, 7, 7)).authoritative == [("RG-1.187", "2")]
    assert rg187.get("RG-1.187", "2").issue_date == date(2020, 6, 1)    # issue date untouched


def test_one_guide_split_into_two_successors_resolves_to_both():
    # 81 FR 88710: RGs 1.3/1.4/1.5 withdrawn, guidance "now incorporated into RG 1.183 ... and
    # RG 1.195". One subject, two governing successors — why resolve_authority returns a list.
    s = AuthorityStore()
    for doc, rev, eff, status in (("RG-1.3", "2", date(1974, 6, 1), "withdrawn"),
                                  ("RG-1.183", "0", date(2000, 7, 1), "active"),
                                  ("RG-1.195", "0", date(2003, 5, 1), "active")):
        s.upsert_document(DocumentVersion(doc_id=doc, revision=rev, kind="reg_guide",
                                          status=status, effective_from=eff))
    text = "These three RGs are being withdrawn because the guidance contained in them has been superseded and is now incorporated into RG 1.183, ‘‘Alternative Radiological Source Terms for Evaluating Design Basis Accidents at Nuclear Power Reactors,’’ and RG 1.195, ‘‘Methods and Assumptions for Evaluating Radiological Consequences of Design Basis Accidents at Light-Water Nuclear Power Reactors.’’"   # verbatim, 81 FR 88710
    for e in parse_evidence("RG-1.3", "2", text, stated_date=date(2016, 12, 8)):
        s.add_edge(resolve_wildcards(e, s))
    assert s.resolve_authority("RG-1.3", date(2017, 1, 1)).authoritative == [
        ("RG-1.183", "0"), ("RG-1.195", "0")]
    assert s.resolve_authority("RG-1.3", date(2016, 1, 1)).authoritative[0] == ("RG-1.3", "2")


def test_two_sources_disagreeing_on_end_date_make_the_window_ambiguous():
    # NRC index: RG 1.85 Rev 31 guidance incorporated into RG 1.84 Rev 32 in 06/2003.
    # 69 FR 34202 / index: RG 1.85 withdrawn 06/18/2004. Between the two, the record is ambiguous.
    s = AuthorityStore()
    for doc, rev, eff in (("RG-1.85", "31", date(1999, 5, 1)), ("RG-1.84", "32", date(2003, 6, 1))):
        s.upsert_document(DocumentVersion(doc_id=doc, revision=rev, kind="reg_guide",
                                          status="active", effective_from=eff))
    withdrawn = ("Materials code case Acceptability - - ASME Section III, Division 1(Withdrawn See -- "
                 "69 FR 34202 06/18/2004)")
    for text in (RG185_INDEX, withdrawn):
        for e in parse_evidence("RG-1.85", "31", text):
            s.add_edge(e)
    assert s.resolve_authority("RG-1.85", date(2003, 1, 1)).resolved                 # before both
    assert not s.resolve_authority("RG-1.85", date(2003, 9, 1)).resolved             # in between
    after = s.resolve_authority("RG-1.85", date(2004, 7, 1))
    assert after.resolved and after.authoritative == [("RG-1.84", "32")]            # after both


def test_active_voice_incorporation_is_not_a_self_withdrawal():
    # NRC RG index history for RG 1.78 (verbatim). Mentions "withdrawn", but about RG 1.95.
    text = "Revision 1 incorporates guidance from withdrawn Regulatory Guide 1.95"
    [e] = parse_evidence("RG-1.78", "1", text, stated_date=date(2001, 12, 1))
    assert (e.from_doc, e.from_rev, e.to_doc, e.edge_type) == ("RG-1.78", "1", "RG-1.95", "cross_family")


# Adapted from supersession_evidence.jsonl line 26 (RG 1.95 withdrawal notice, 67 FR 939): the
# "incorporated into" clause is dropped so only the withdrawal of a *different* guide (RG 1.95)
# remains. Parsed against RG 1.187 rev 2, which the sentence does not name.
WITHDRAWS_OTHER_GUIDE = ("With the publication of Regulatory Guide 1.78, Regulatory Guide 1.95, "
                         "‘‘Protection of Nuclear Power Plant Control Room Operators Against an "
                         "Accidental Chlorine Release,’’ is being withdrawn.")
# Not a verbatim NRC string: no real row hits this branch. Shape of a CFR page-replacement
# instruction with no source document identified.
PAGE_SWAP_NO_TARGET = "Remove pages 3-4 and insert new pages 3-4 as follows."
# Not verbatim: active-voice supersession naming no document.
SUPERSEDES_NOTHING_NAMED = "This revision supersedes the previous guidance."


def _blocking(text):
    [e] = parse_evidence("RG-1.187", "2", text)
    store = AuthorityStore()
    store.add_edge(e)
    return e, store.unresolved_into("RG-1.187", "2")


def test_withdrawal_of_a_different_guide_blocks_the_subject():
    e, blocking = _blocking(WITHDRAWS_OTHER_GUIDE)
    assert (e.to_doc, e.to_rev, e.confidence) == ("RG-1.187", "2", "unresolved")
    assert blocking == [e]


def test_page_swap_without_named_target_blocks_the_subject():
    e, blocking = _blocking(PAGE_SWAP_NO_TARGET)
    assert (e.to_doc, e.to_rev, e.confidence) == ("RG-1.187", "2", "unresolved")
    assert blocking == [e]


def test_supersedes_with_no_named_target_deliberately_does_not_block_the_subject():
    # Deliberate: the subject replaced something unnamed. That is ambiguity about the old side,
    # not about whether the subject governs, so unresolved_into must NOT see it. Do not "fix".
    e, blocking = _blocking(SUPERSEDES_NOTHING_NAMED)
    assert (e.from_doc, e.to_doc) == ("RG-1.187", "UNKNOWN")
    assert blocking == []
