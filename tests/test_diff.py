"""Paragraph extraction on verbatim chunk text read from the Chroma index (10CFR50.59@1996-08-28)."""

from datetime import date

import pytest

from kanon.diff import (DiffResult, Paragraph, diff_as_of, diff_paragraphs, format_diff,
                        normalize, paragraphs, section_region)
from kanon.ingest import Chunk
from kanon.store import AuthorityStore, DocumentVersion, Edge

R1996 = (
    "[27 FR 12186, Dec. 8, 1962, as amended at 35 FR 11461, July 17, 1970]\n"
    "§50.59 Changes, tests and experiments.\n"
    "(a)(1) The holder of a license authorizing operation of a production or utilization "
    "facility may (i) make changes in the facility as described in the safety analysis report, "
    "(ii) make changes in the procedures as described in the safety analysis report, and "
    "(iii) conduct tests or experiments not described in the safety analysis report.\n"
    "(2) A proposed change, test, or experiment shall be deemed to involve an unreviewed safety "
    "question (i) if the probability of occurrence or the consequences of an accident or "
    "malfunction of equipment important to safety previously evaluated in the safety analysis "
    "report may be increased.\n"
    "§50.60 Acceptance criteria for fracture prevention measures for lightwater nuclear "
    "power reactors for normal operation.\n"
    "(a) Except as provided in paragraph (b) of this section, all light-water nuclear power "
    "reactors must meet the fracture toughness requirements.\n")


def chunk(text: str, page: int) -> Chunk:
    return Chunk(id=f"10CFR50.59@1996-08-28#{page:04d}", text=text, doc_id="10CFR50.59",
                 revision="1996-08-28", kind="regulation", status="superseded",
                 effective_from="1996-08-28", effective_to=None, issue_date="1996-08-28",
                 section="x", page=page, accession="", source_url="", paginated=True)


def test_section_region_drops_neighbouring_sections():
    # the 1996 CFR-edition chunk carries 50.58/50.60/50.61 text under a 50.59 doc_id
    region = section_region(R1996)
    assert "unreviewed safety question" in region
    assert "fracture toughness" not in region
    assert "27 FR 12186" not in region


def test_section_region_empty_when_section_absent():
    assert section_region("(a) Some unrelated text with no section heading.") == ""


def test_paragraph_keys_and_hierarchy():
    out = paragraphs([chunk(R1996, page=1)])
    assert set(out) == {"(a)(1)", "(a)(2)"}          # bare "(2)" inherits parent "(a)"
    assert out["(a)(2)"].text.startswith("A proposed change")
    assert out["(a)(2)"].page == 1
    assert "(i) if the probability" in out["(a)(2)"].text   # inline romans stay in the body


def test_paragraph_spanning_two_chunks_is_joined():
    first = chunk("§ 50.59 Changes, tests, and experiments.\n(c)(1) A licensee may make "
                  "changes in the facility", page=1)
    second = chunk("§ 50.59 Changes, tests, and experiments.\nwithout obtaining a license "
                   "amendment pursuant to § 50.90 only if:", page=2)
    out = paragraphs([first, second])
    assert "without obtaining a license amendment" in out["(c)(1)"].text
    assert out["(c)(1)"].page == 1                   # the page where the paragraph starts


def test_normalize_ignores_whitespace_and_quote_style():
    assert normalize("the  ''Code'',\nas  updated") == normalize("the “Code”, as updated")


def test_headingless_continuation_chunks_stay_in_section_regression_1996():
    # real 1996 shape: heading in one chunk, (a)(2)/(b)/(c) in heading-less chunks after it
    chunks = [
        chunk("(a) Each licensee shall do the fifty-eight thing.\n§50.58 Hearings.\n(b) Fifty-eight text.", 1),
        chunk("§50.59 Changes, tests and experiments.\n(a)(1) The holder may make changes.", 2),
        chunk("(2) A proposed change shall be deemed an unreviewed safety question.\n"
              "(b)(1) The holder shall maintain records.", 3),
        chunk("(c) The Commission may require an amendment.", 4),
        chunk("§50.60 Acceptance criteria for fracture prevention.\n(a) Fracture toughness text.", 5),
    ]
    out = paragraphs(chunks)
    assert set(out) == {"(a)(1)", "(a)(2)", "(b)(1)", "(c)"}
    blob = " ".join(p.text for p in out.values())
    assert "fifty-eight" not in blob.lower() and "Fifty-eight" not in blob
    assert "Fracture" not in blob and "fracture" not in blob


def test_headingless_chunk_before_any_heading_contributes_nothing():
    assert paragraphs([chunk("(a)(1) Orphan text with no heading seen yet.", 1)]) == {}


def test_chunk_prefix_and_print_furniture_never_enter_paragraph_text():
    text = ("Document: 10CFR50.59 | revision: 2001-03-13 | type: regulation | title: x\n"
            "§50.59 Changes, tests, and experiments.\n"
            "(a)(1) The holder may make changes, and\n"
            "VerDate 11<MAY>2000 08:17 Feb 22, 2001 Jkt 194000 PO 00000 Frm 00001 Fmt 8010 Sfmt 8010\n"
            "42\n"
            "continue as described.")
    out = paragraphs([chunk(text, 1)])
    blob = " ".join(p.text for p in out.values())
    assert "continue as described" in blob
    assert "Document:" not in blob and "VerDate" not in blob and "42" not in blob


def test_bare_running_head_does_not_attribute_foreign_tail_regression_2001():
    # real chunk-0 shape: prefix, bare "§ 50.59" running head, then another section's tail
    text = ("Document: 10CFR50.59 | revision: 2001-03-13 | type: regulation | title: x\n"
            "§ 50.59\n"
            "- (6) No petition or request for review will be entertained by the Commission.\n")
    assert paragraphs([chunk(text, 1)]) == {}


# --- Task 3: date-to-date diff -------------------------------------------------------------------

R2025 = ("§ 50.59 Changes, tests, and experiments.\n"
         "(a)(1) Change means a modification or addition to, or removal from, the facility or "
         "procedures that affects a design function, method of performing or controlling the "
         "function, or an evaluation that demonstrates that intended functions will be accomplished.\n"
         "(a)(2) Departure from a method of evaluation described in the FSAR (as updated) used in "
         "establishing the design bases or in the safety analyses means changing any of the "
         "elements of the method described in the FSAR.\n"
         "(b) Licensees shall maintain records of changes in the facility.\n")


def p(key, text):
    return Paragraph(key=key, text=text, page=1, doc_id="10CFR50.59", revision="1996-08-28")


class FakeIndex:
    def __init__(self, texts):
        self.texts = texts

    def chunks_for(self, doc_key):
        t = self.texts.get(doc_key)
        if t is None:
            return []
        rev = doc_key.split("@")[1]
        c = chunk(t, 1)
        return [c.model_copy(update={"revision": rev, "id": f"{doc_key}#0001"})]


def _edge(frm, to, when, typ="full"):
    return Edge(from_doc=frm[0], from_rev=frm[1], to_doc=to[0], to_rev=to[1], edge_type=typ,
                valid_from=when, evidence="x", confidence="explicit")


@pytest.fixture
def store():
    s = AuthorityStore()
    for rev, eff in (("1996-08-28", date(1996, 8, 28)), ("2001-03-13", date(2001, 3, 13)),
                     ("2025-01-29", date(2025, 1, 29))):
        s.upsert_document(DocumentVersion(doc_id="10CFR50.59", revision=rev, kind="regulation",
                                          status="active", effective_from=eff))
    a, b, c = (("10CFR50.59", r) for r in ("1996-08-28", "2001-03-13", "2025-01-29"))
    s.add_edge(_edge(b, a, date(2001, 3, 13)))
    s.add_edge(_edge(c, b, date(2025, 1, 29)))
    for rev, eff in (("1", date(2000, 1, 1)), ("2", date(2020, 7, 7))):
        s.upsert_document(DocumentVersion(doc_id="RG-1.187", revision=rev, kind="reg_guide",
                                          status="active", effective_from=eff))
    s.add_edge(_edge(("RG-1.187", "2"), ("RG-1.187", "1"), date(2020, 7, 7)))
    for doc, rev, eff in (("RG-1.85", "31", date(1999, 5, 1)), ("RG-1.84", "32", date(2003, 6, 1))):
        s.upsert_document(DocumentVersion(doc_id=doc, revision=rev, kind="reg_guide",
                                          status="active", effective_from=eff))
    s.add_edge(_edge(("RG-1.84", "32"), ("RG-1.85", "31"), date(2003, 6, 1), "cross_family")
               .model_copy(update={"confidence": "inferred"}))
    s.add_edge(_edge(("RG-1.85", "31"), ("RG-1.85", "31"), date(2004, 6, 18), "withdrawn"))
    return s


@pytest.fixture
def index():
    return FakeIndex({"10CFR50.59@1996-08-28": R1996, "10CFR50.59@2001-03-13": R2025,
                      "10CFR50.59@2025-01-29": R2025})


def test_diff_paragraphs_classifies_gone_added_changed():
    old = {"(a)(2)": p("(a)(2)", "unreviewed safety question"), "(c)": p("(c)", "who desires")}
    new = {"(a)(1)": p("(a)(1)", "Change means a modification"), "(c)": p("(c)", "only if:")}
    gone, added, changed = diff_paragraphs(old, new)
    assert [g.key for g in gone] == ["(a)(2)"]
    assert [a.key for a in added] == ["(a)(1)"]
    assert [d.key for d in changed] == ["(c)"]


def test_identical_text_with_different_whitespace_is_not_changed():
    old = {"(a)": p("(a)", "the  ''Code'',\nas updated")}
    new = {"(a)": p("(a)", "the “Code”, as updated")}
    assert diff_paragraphs(old, new)[2] == []


def test_diff_as_of_names_both_governing_revisions(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(2000, 6, 1), date(2026, 11, 15))
    assert r.from_revisions == ["1996-08-28"] and r.to_revisions == ["2025-01-29"]
    # same designator, different requirement: (a)(2) is CHANGED, not gone
    delta = next(d for d in r.changed if d.key == "(a)(2)")
    assert "unreviewed safety question" in delta.old.text
    assert "Departure from a method of evaluation" in delta.new.text
    assert [g.key for g in r.gone] == []   # R1996 fixture holds only (a)(1)/(a)(2); both exist in 2025
    assert [a.key for a in r.added] == ["(b)"]
    assert [(e.from_doc, e.from_rev) for e in r.ended_by] == [("10CFR50.59", "2001-03-13")]


def test_same_date_both_sides_is_an_empty_diff(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(2000, 6, 1), date(2000, 6, 1))
    assert (r.gone, r.added, r.changed) == ([], [], [])
    assert r.from_revisions == r.to_revisions == ["1996-08-28"]


def test_date_before_earliest_revision_says_nothing_governed(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(1996, 4, 1), date(2026, 11, 15))
    assert r.from_revisions == []
    assert any("nothing governed on 1996-04-01" in n for n in r.notes)
    assert r.gone == []                       # not "nothing changed"


def test_metadata_only_document_reports_no_text(index, store):
    r = diff_as_of(index, store, "RG-1.187", date(2019, 12, 1), date(2020, 8, 1))
    assert any("no text indexed" in n for n in r.notes)
    assert (r.gone, r.added, r.changed) == ([], [], [])


def test_conflict_window_surfaces_unresolved_instead_of_diffing(index, store):
    r = diff_as_of(index, store, "RG-1.85", date(2003, 9, 1), date(2026, 11, 15))
    assert r.unresolved
    assert any("authority is unresolved" in n for n in r.notes)
    assert (r.gone, r.added, r.changed) == ([], [], [])


def test_format_diff_labels_every_line_with_revision_and_page():
    r = DiffResult(doc_id="10CFR50.59", from_as_of=date(2000, 6, 1), to_as_of=date(2026, 11, 15),
                   from_revisions=["1996-08-28"], to_revisions=["2025-01-29"],
                   gone=[p("(a)(2)", "A proposed change shall be deemed")], added=[], changed=[],
                   ended_by=[], unresolved=[], notes=[],
                   kinds={"10CFR50.59@1996-08-28": "regulation", "10CFR50.59@2025-01-29": "regulation"})
    text = format_diff(r)
    assert "GONE" in text and "(a)(2)" in text and "1996-08-28" in text and "p.1" in text
    assert "requirement" in text
