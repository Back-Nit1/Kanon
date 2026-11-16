

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
