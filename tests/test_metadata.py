"""Structured applicability metadata: deterministic, nullable, and filter-ready."""

from datetime import date

from kanon.agent import QueryClass, _question_controls
from kanon.ingest import Chunk, _apply_document_metadata
from kanon.store import AuthorityStore, DocumentVersion


def _doc(**overrides) -> DocumentVersion:
    values = dict(doc_id="QP 1104", revision="4", family="QP 1104", kind="other",
                  title="Pump Replacement Qualification Procedure", status="active",
                  effective_from=date(2024, 1, 1))
    values.update(overrides)
    return DocumentVersion(**values)


def _chunk(text: str, page: int = 1) -> Chunk:
    return Chunk(id="QP 1104@4#0000", text=text, doc_id="QP 1104", revision="4",
                 kind="other", status="active", effective_from="2024-01-01",
                 effective_to=None, issue_date="", section="Cover", page=page,
                 accession="", source_url="", paginated=True)


def test_authority_store_round_trips_multi_unit_applicability():
    store = AuthorityStore()
    original = _doc(plants=["Example Nuclear Station"], units=["1", "2"],
                    dockets=["50-001", "50-002"])
    store.upsert_document(original)
    got = store.get(original.doc_id, original.revision)
    assert got.plants == ["Example Nuclear Station"]
    assert got.units == ["1", "2"]
    assert got.dockets == ["50-001", "50-002"]
    assert got.resolved_applicability_scope() == "facility"


def test_global_scope_must_be_explicit():
    assert _doc(kind="regulation").resolved_applicability_scope() == "global"
    assert _doc(kind="regulation", applicability_scope="global").resolved_applicability_scope() == "global"
    assert _doc(kind="other").resolved_applicability_scope() == "unknown"


def test_layout_labels_are_extracted_before_identifier_header_is_added():
    chunks = _apply_document_metadata([_chunk(
        "Plant: Example Nuclear Station\nUnits: 1 and 2\nDocket Number: 50-123\nPump replacement")],
        _doc())
    c = chunks[0]
    assert c.plants == ["Example Nuclear Station"]
    assert c.units == ["1", "2"]
    assert c.dockets == ["50-123"]
    assert c.applicability_scope == "facility"
    assert c.applicability_source == "layout"
    # Exact identifiers and fuzzy body text are both in the BM25/dense input.
    assert c.text.startswith("Document: QP 1104 | revision: 4")
    assert "Pump replacement" in c.text


def test_structured_metadata_wins_over_layout_text():
    c = _apply_document_metadata(
        [_chunk("Wrong Nuclear Power Plant\nUnit: 9\nDocket: 50-999")],
        _doc(plants=["Approved Nuclear Station"], units=["1"], dockets=["50-001"]))[0]
    assert (c.plants, c.units, c.dockets) == (
        ["Approved Nuclear Station"], ["1"], ["50-001"])
    assert c.applicability_source == "authority_store"


def test_unlabeled_mentions_and_later_pages_do_not_set_applicability():
    chunks = _apply_document_metadata([
        _chunk("Example Nuclear Station\nA reference to Docket Number 50-123."),
        _chunk("Plant: Other Nuclear Station\nUnit: 2", page=2),
    ], _doc())
    assert chunks[0].applicability_scope == "unknown"
    assert chunks[0].plants == [] and chunks[0].dockets == []


def test_unknown_arrays_round_trip_through_chroma_scalar():
    c = _chunk("text")
    metadata = c.chroma_metadata()
    assert metadata["plants"] == "[]"
    restored = Chunk.from_chroma(c.id, c.text, metadata)
    assert restored.plants == [] and restored.units == [] and restored.dockets == []


def test_explicit_unit_and_docket_cannot_be_dropped_by_tool_selection():
    controls = _question_controls(
        "What governs pump replacement for Unit 2, Docket 50-333?",
        QueryClass(kind="current", needs_requirements=False))
    assert controls == {"needs_requirements": True, "plant": None,
                        "unit": "2", "docket": "50-333"}

