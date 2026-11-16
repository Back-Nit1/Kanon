

def test_precedent_only_question_does_not_need_requirement_citation():
    from kanon.agent import _needs_requirements
    assert not _needs_requirements("Has a licensee been cited for an inadequate 10 CFR 50.59 evaluation?", True)
    # requirement wording keeps the check on, even with 'violation' in the question
    assert _needs_requirements("Which requirement governs a violation of 10 CFR 50.59?", False)
    assert _needs_requirements("In 2001, how did 10 CFR 50.59 define a change?", False)


# --- paragraph_not_in_revision (integrity rule) -------------------------------------------------
from datetime import date

from kanon.agent import AUTHORITY_RULES, Citation, check_citations
from kanon.ingest import Chunk
from kanon.store import AuthorityStore, DocumentVersion

AS_OF = date(2026, 11, 15)
K2025 = "(a) (a)(1) (a)(2) (a)(2)(i) (a)(2)(ii) (a)(3) (a)(3)(i) (a)(3)(ii) (a)(3)(iii) (a)(4) (a)(5) (a)(6) (a)(6)(i) (a)(6)(ii) (b) (c)(1) (c)(1)(i) (c)(1)(ii) (c)(2) (c)(2)(i) (c)(2)(ii) (c)(2)(iii) (c)(2)(iv) (c)(2)(v) (c)(2)(vi) (c)(2)(vii) (c)(2)(viii) (c)(3) (c)(4) (d)(1) (d)(2) (d)(3)".split()
K1996 = "(a)(1) (a)(2) (b)(1) (b)(2) (b)(3) (c) (d) (e)".split()
KEYS = {"2025-01-29": K2025, "1996-08-28": K1996}


def _resolver(calls=None):
    def r(doc_id, revision):
        if calls is not None:
            calls.append((doc_id, revision))
        return {k: None for k in KEYS.get(revision, [])}   # only keys are consulted
    return r


def _store():
    s = AuthorityStore()
    for rev, eff in (("1996-08-28", date(1996, 8, 28)), ("2025-01-29", date(2025, 1, 29))):
        s.upsert_document(DocumentVersion(doc_id="10CFR50.59", revision=rev, kind="regulation",
                                          status="active", effective_from=eff))
    return s


def _retrieved(rev):
    return [Chunk(id=f"10CFR50.59@{rev}#0001", text="x", doc_id="10CFR50.59", revision=rev,
                  kind="regulation", status="active", effective_from=rev, effective_to=None,
                  issue_date=rev, section="x", page=1, accession="", source_url="", paginated=True)]


def _rules(section, rev="2025-01-29", paragraphs=_resolver(), as_of=AS_OF, kind="time_anchored"):
    cit = Citation(doc_id="10CFR50.59", revision=rev, section=section, page=1)
    return [v.rule for v in check_citations(_store(), [cit], _retrieved(rev), as_of, kind,
                                            paragraphs=paragraphs)]


def test_paragraph_not_in_revision_blocks_an_invented_paragraph():
    assert "paragraph_not_in_revision" in _rules("§ 50.59(z)(9)")


def test_paragraph_present_in_revision_is_clean():
    assert "paragraph_not_in_revision" not in _rules("§ 50.59(a)(2)")


def test_unparseable_or_missing_section_is_not_a_violation():
    for section in (None, "Changes, tests, and experiments", "a.2", "§ 50.59"):
        assert "paragraph_not_in_revision" not in _rules(section)


def test_paragraph_rule_is_an_integrity_rule_not_an_authority_rule():
    assert "paragraph_not_in_revision" not in AUTHORITY_RULES   # therefore blocks in every mode


def test_paragraph_rule_is_skipped_when_no_resolver_given():
    assert "paragraph_not_in_revision" not in _rules("§ 50.59(z)(9)", paragraphs=None)


def test_real_key_sets_differ_by_revision():
    # (d)(1) exists only in 2025-01-29; (e) exists only in 1996-08-28
    assert "paragraph_not_in_revision" not in _rules("§ 50.59(d)(1)", rev="2025-01-29")
    assert "paragraph_not_in_revision" in _rules("§ 50.59(d)(1)", rev="1996-08-28", as_of=date(1999, 1, 1))
    assert "paragraph_not_in_revision" not in _rules("§ 50.59(e)", rev="1996-08-28", as_of=date(1999, 1, 1))
    assert "paragraph_not_in_revision" in _rules("§ 50.59(e)", rev="2025-01-29")


def test_violation_detail_names_designator_revision_and_available_keys():
    cit = Citation(doc_id="10CFR50.59", revision="1996-08-28", section="§ 50.59(d)(1)", page=1)
    v = [v for v in check_citations(_store(), [cit], _retrieved("1996-08-28"), date(1999, 1, 1),
                                    "time_anchored", paragraphs=_resolver())
         if v.rule == "paragraph_not_in_revision"][0]
    assert "(d)(1)" in v.detail and "1996-08-28" in v.detail and "(b)(1)" in v.detail
    assert v.doc_id == "10CFR50.59" and v.revision == "1996-08-28"


def test_record_and_unknown_document_skip_paragraph_rule():
    s = _store()
    s.upsert_document(DocumentVersion(doc_id="IR-1", revision="1", kind="operating_experience",
                                      status="active", effective_from=date(2020, 1, 1)))
    cit = Citation(doc_id="IR-1", revision="1", section="(z)(9)", page=1)
    calls = []
    out = check_citations(s, [cit], [], AS_OF, "current", paragraphs=_resolver(calls))
    assert "paragraph_not_in_revision" not in [v.rule for v in out] and calls == []
    ghost = Citation(doc_id="NOPE", revision="1", section="(z)(9)", page=1)
    out = check_citations(s, [ghost], [], AS_OF, "current", paragraphs=_resolver(calls))
    assert [v.rule for v in out] == ["unknown_document"] and calls == []


# --- Task 5: citation provenance and the explained block ----------------------------------------
from kanon.agent import Violation, abstention_text, citation_notes
from kanon.store import Edge
from tests.test_diff import R1996, R2025, FakeIndex


def _t5_store():
    s = AuthorityStore()
    for rev, eff in (("1996-08-28", date(1996, 8, 28)), ("2001-03-13", date(2001, 3, 13)),
                     ("2025-01-29", date(2025, 1, 29))):
        s.upsert_document(DocumentVersion(doc_id="10CFR50.59", revision=rev, kind="regulation",
                                          status="active", effective_from=eff))
    for new, old, when in (("2001-03-13", "1996-08-28", date(2001, 3, 13)),
                           ("2025-01-29", "2001-03-13", date(2025, 1, 29))):
        s.add_edge(Edge(from_doc="10CFR50.59", from_rev=new, to_doc="10CFR50.59", to_rev=old,
                        edge_type="full", valid_from=when, evidence="x", confidence="explicit"))
    s.upsert_document(DocumentVersion(doc_id="EA-98-155", revision="0", kind="operating_experience",
                                      status="active", effective_from=date(1998, 1, 1)))
    s.upsert_document(DocumentVersion(doc_id="RG-1.187", revision="2", kind="reg_guide",
                                      status="active", effective_from=date(2020, 7, 7)))
    return s


def _t5_index():
    return FakeIndex({"10CFR50.59@1996-08-28": R1996, "10CFR50.59@2001-03-13": R1996,
                      "10CFR50.59@2025-01-29": R2025})


_V2025 = Violation(rule="not_governing_on_as_of", doc_id="10CFR50.59", revision="2025-01-29",
                   detail="did not govern on 2001-04-01")


def test_citation_notes_carry_the_three_developer_facts():
    line = citation_notes(_t5_store(), [Citation(doc_id="10CFR50.59", revision="2001-03-13", page=1)],
                          date(2001, 4, 1))[0]
    assert "requirement" in line
    assert "governed 2001-03-13..2025-01-29" in line or "governed 2001-03-13..open" in line
    assert "as_of=2001-04-01" in line
    assert "supersedes: 10CFR50.59 Rev 1996-08-28" in line


def test_citation_notes_omit_supersedes_when_nothing_superseded():
    line = citation_notes(_t5_store(), [Citation(doc_id="10CFR50.59", revision="1996-08-28", page=1)],
                          date(1999, 1, 1))[0]
    assert "supersedes" not in line


def test_record_citation_is_labelled_a_record_not_a_requirement():
    line = citation_notes(_t5_store(), [Citation(doc_id="EA-98-155", revision="0", page=1)],
                          date(2001, 4, 1))[0]
    assert "record" in line and "requirement" not in line


def test_abstain_message_names_what_governs_on_the_asked_date():
    out = abstention_text(_t5_index(), _t5_store(), [_V2025], date(2001, 4, 1))
    assert "[not_governing_on_as_of] did not govern on 2001-04-01" in out   # reason kept
    assert "10CFR50.59@2001-03-13" in out
    assert "GONE" in out or "CHANGED" in out


def test_abstain_says_plainly_when_nothing_governed():
    out = abstention_text(_t5_index(), _t5_store(), [_V2025], date(1990, 1, 1))
    assert "nothing governed" in out and "[not_governing_on_as_of]" in out


def test_abstain_still_explains_when_diff_cannot_be_computed():
    v = Violation(rule="superseded", doc_id="RG-1.187", revision="1", detail="superseded by Rev 2")
    s = _t5_store()
    s.upsert_document(DocumentVersion(doc_id="RG-1.187", revision="1", kind="reg_guide",
                                      status="superseded", effective_from=date(2000, 1, 1)))
    s.add_edge(Edge(from_doc="RG-1.187", from_rev="2", to_doc="RG-1.187", to_rev="1",
                    edge_type="full", valid_from=date(2020, 7, 7), evidence="x", confidence="explicit"))
    out = abstention_text(_t5_index(), s, [v], date(2026, 11, 15))   # no text indexed for a reg guide
    assert "[superseded] superseded by Rev 2" in out and "RG-1.187@2" in out

    class Boom:
        def chunks_for(self, key):
            raise RuntimeError("chroma down")
    out = abstention_text(Boom(), _t5_store(), [_V2025], date(2001, 4, 1))
    assert "[not_governing_on_as_of]" in out and "10CFR50.59@2001-03-13" in out
