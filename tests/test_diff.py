"""Paragraph extraction on verbatim chunk text read from the Chroma index (10CFR50.59@1996-08-28)."""

from kanon.diff import normalize, paragraphs, section_region
from kanon.ingest import Chunk

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
