"""Corpus ingest: Docling parse -> page-anchored chunks carrying full authority metadata.

Parser choice: Docling.
  - MIT, runs locally. Controlled nuclear documents often can't be sent to a third-party cloud
    parser, so self-hostable is the defensible default even where a hosted one scores higher.
  - Strong multi-column reading order, and it keeps page provenance per item. Page anchoring
    is a hard requirement: a citation to the right revision of a 200-page NUREG with no page
    is not auditable.
  Rejected: LlamaParse — better on dense tables/hard scans (vision model per page) but hosted,
    per-page billed, cloud-only. Revisit only if table extraction becomes the bottleneck.
  Rejected: Unstructured — broadest format support, but dropped page numbers in a published
    TOC benchmark (disqualifying here) and markedly slower.

Chunking: Docling's HybridChunker — structure-aware (splits on the document's own headings,
then by token budget), and every chunk keeps the doc items it came from, which is where the
page number comes from. A fixed-size character splitter would cut across sections and lose
provenance.

OCR is off: degraded scans are out of scope (see KANON.md). The corpus is restricted to PDFs
with a text layer; a scan yields no chunks and fails Tier A loudly instead of yielding garbage.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from docling.chunking import HybridChunker
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from pydantic import BaseModel, Field

from kanon.store import DocumentVersion


class Chunk(BaseModel):
    """One retrievable unit. Every field below is required metadata (Tier A checks them)."""
    id: str
    text: str
    doc_id: str
    revision: str
    kind: str
    status: str
    effective_from: str
    effective_to: str | None
    issue_date: str
    section: str
    page: int                       # REQUIRED. 1-based page of the fetched source PDF (not the
                                    # printed page label), so a reviewer opening that file lands on it.
    accession: str
    source_url: str
    paginated: bool                 # False only for HTML-native sources (eCFR, NRC web pages)
    title: str = ""
    family: str = ""
    plants: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    dockets: list[str] = Field(default_factory=list)
    applicability_scope: str = "unknown"
    applicability_source: str = "unknown"
    score: float | None = None      # filled in by retrieval

    @property
    def doc_key(self) -> str:
        return f"{self.doc_id}@{self.revision}"

    def chroma_metadata(self) -> dict:
        # Chroma rejects None metadata values, so None round-trips as "".
        meta = self.model_dump(exclude={"id", "text", "score"})
        meta["doc_key"] = self.doc_key
        return {k: (json.dumps(v) if isinstance(v, list)
                    else "" if v is None else v) for k, v in meta.items()}

    @classmethod
    def from_chroma(cls, id: str, text: str, meta: dict, score: float | None = None) -> "Chunk":
        fields = {k: (json.loads(v) if k in {"plants", "units", "dockets"} and isinstance(v, str)
                      else [] if v == ["__unknown__"] else
                      None if v == "" and k == "effective_to" else v)
                  for k, v in meta.items() if k != "doc_key"}
        return cls(id=id, text=text, score=score, **fields)


REQUIRED_KEYS = set(Chunk.model_fields) - {"score"}

# Part of every ingest job's idempotency key. Bump it whenever chunking output changes (splitting,
# heading handling, metadata), so the next `kanon ingest` re-queues every document instead of
# skipping them as already done.
CHUNKING_VERSION = 3


def make_converter() -> DocumentConverter:
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def chunk_document(path: Path, doc: DocumentVersion, paginated: bool,
                   converter: DocumentConverter, chunker: HybridChunker,
                   page_range: tuple[int, int] | None = None) -> list[Chunk]:
    """page_range: a Federal Register issue PDF bundles unrelated agencies' documents (SBA, FAA
    airworthiness directives) around the NRC one; only the NRC pages may carry an NRC doc_id."""
    if path.suffix in (".xml", ".txt"):
        return _chunk_plain(path, doc)
    parsed = converter.convert(path, page_range=page_range or (1, 10**9)).document
    chunks = []
    for i, c in enumerate(chunker.chunk(parsed)):
        pages = [p.page_no for item in c.meta.doc_items for p in item.prov]
        if pages:
            page = min(pages)
        elif not paginated:
            # HTML sources have no pages: one logical page, and the manifest records
            # paginated=False so a reviewer knows page=1 is a convention, not a PDF location.
            page = 1
        else:
            raise ValueError(f"{path.name} chunk {i}: paginated source but no page provenance")
        # Heading path goes into both the metadata and the embedded text, so BM25 and dense
        # search see "§ 50.59 > (c)" with the body. Text above the first heading (a Federal
        # Register preamble, an untitled page 1) falls back to the document title, never blank.
        section = " > ".join(c.meta.headings or []) or doc.title or doc.doc_id
        text = chunker.contextualize(c)
        chunks.append(Chunk(
            id=f"{doc.doc_id}@{doc.revision}#{i:04d}",
            text=text if c.meta.headings else f"{section}\n{text}",
            doc_id=doc.doc_id, revision=doc.revision, kind=doc.kind, status=doc.status,
            effective_from=_s(doc.effective_from), effective_to=_s(doc.effective_to) or None,
            issue_date=_s(doc.issue_date), section=section,
            page=page, accession=doc.accession or "", source_url=doc.source_url or "",
            paginated=paginated, title=doc.title or "", family=doc.family or ""))
    return _apply_document_metadata(chunks, doc)


PLAIN_CHUNK_CHARS = 1500   # ~350 tokens: same order as HybridChunker's default budget


def _chunk_plain(path: Path, doc: DocumentVersion) -> list[Chunk]:
    """eCFR XML and plain text: no layout to recover and no pages, so Docling buys nothing.
    Stdlib parse, then pack whole paragraphs (never split one) into ~PLAIN_CHUNK_CHARS chunks.
    page=1 by convention; these sources carry paginated=False in the manifest."""
    if path.suffix == ".xml":
        # eCFR section XML: <HEAD> is the section title, <P> the paragraphs. Python's expat
        # refuses external entities and caps entity expansion, so untrusted XML is safe to parse.
        root = ET.parse(path).getroot()
        section = "".join(root.findtext("HEAD") or "").strip()
        paras = ["".join(p.itertext()).strip() for p in root.iter("P")]
    else:
        section = ""
        paras = [p.strip() for p in path.read_text().split("\n\n")]
    section = section or doc.title or doc.doc_id
    groups, cur = [], ""
    for para in filter(None, paras):
        if cur and len(cur) + len(para) > PLAIN_CHUNK_CHARS:
            groups.append(cur)
            cur = ""
        cur = f"{cur}\n{para}".strip()
    groups += [cur] if cur else []
    chunks = [Chunk(id=f"{doc.doc_id}@{doc.revision}#{i:04d}",
                  text=f"{section}\n{g}".strip(), doc_id=doc.doc_id, revision=doc.revision,
                  kind=doc.kind, status=doc.status, effective_from=_s(doc.effective_from),
                  effective_to=_s(doc.effective_to) or None, issue_date=_s(doc.issue_date),
                  section=section, page=1, accession=doc.accession or "",
                  source_url=doc.source_url or "", paginated=False,
                  title=doc.title or "", family=doc.family or "")
            for i, g in enumerate(groups)]
    return _apply_document_metadata(chunks, doc)


# Strict, label-oriented patterns only. Free-form entity inference would turn a document that
# merely mentions another plant into one that applies to that plant, which is unsafe for a hard
# filter. Ambiguous prose stays unknown.
_PLANT_LINE = re.compile(r"(?im)^[ \t]*(?:Plant|Facility)[ \t]*:[ \t]*([^\n]+?)[ \t]*$")
_DOCKET_LABEL = re.compile(
    r"(?im)^[ \t]*Docket(?:[ \t]+(?:Number|No\.?|ID))?[ \t]*[:#][ \t]*([A-Z0-9][A-Z0-9.-]{2,30})[ \t]*$")
_UNIT_LINE = re.compile(
    r"(?im)^[ \t]*Units?(?:[ \t]+(?:Number|No\.?))?[ \t]*[:#][ \t]*(\d+(?:[ \t]*(?:,|and|&)[ \t]*\d+)*)[ \t]*$")


def _ordered(values) -> list[str]:
    return list(dict.fromkeys(" ".join(str(v).split()) for v in values if str(v).strip()))


def _apply_document_metadata(chunks: list[Chunk], doc: DocumentVersion) -> list[Chunk]:
    """Attach metadata after layout-aware parsing and before any embedding/vector write.

    AuthorityStore fields win. Missing applicability is extracted only from labelled text on
    the first parsed page, so a passing reference to another plant cannot silently change hard
    filter applicability.
    """
    if not chunks:
        return chunks
    first_page_text = "\n".join(c.text for c in chunks if c.page == 1)[:20_000]
    plants, units, dockets = _ordered(doc.plants), _ordered(doc.units), _ordered(doc.dockets)
    extracted = False
    if not plants:
        plants = _ordered(m.group(1) for m in _PLANT_LINE.finditer(first_page_text))
        extracted = extracted or bool(plants)
    if not units:
        units = _ordered(number for m in _UNIT_LINE.finditer(first_page_text)
                         for number in re.findall(r"\d+", m.group(1)))
        extracted = extracted or bool(units)
    if not dockets:
        dockets = _ordered(m.group(1) for m in _DOCKET_LABEL.finditer(first_page_text))
        extracted = extracted or bool(dockets)
    structured = bool(doc.plants or doc.units or doc.dockets)
    source = ("authority_store+layout" if structured and extracted else
              "authority_store" if structured else "layout" if extracted else "unknown")
    scope = "facility" if plants or units or dockets else doc.resolved_applicability_scope()
    header_values = [f"Document: {doc.doc_id}", f"revision: {doc.revision}",
                     f"type: {doc.kind}", f"status: {doc.status}"]
    if doc.title:
        header_values.append(f"title: {doc.title}")
    if plants:
        header_values.append(f"plant: {', '.join(plants)}")
    if units:
        header_values.append(f"unit: {', '.join(units)}")
    if dockets:
        header_values.append(f"docket: {', '.join(dockets)}")
    header = " | ".join(header_values)
    return [c.model_copy(update={"text": f"{header}\n{c.text}", "plants": plants,
                                 "units": units, "dockets": dockets,
                                 "applicability_scope": scope,
                                 "applicability_source": source}) for c in chunks]


def _s(value) -> str:
    return value.isoformat() if value else ""
