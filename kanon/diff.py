"""Deterministic CFR paragraph extraction from chunk text already in the index. No model, no I/O.

Docling `section` metadata cannot key paragraphs (the 1996 CFR-edition chunks are labelled with the
whole-edition title), so designators like (a)(2) are recovered from the text itself, scoped to one
CFR section so §50.58/§50.60/§50.61 text sharing a chunk is never attributed to §50.59.
"""

import re

from pydantic import BaseModel

from kanon.ingest import Chunk


class Paragraph(BaseModel):
    key: str            # "(a)(2)"
    text: str
    page: int           # page of the chunk where the paragraph starts
    doc_id: str
    revision: str


# Headings must open a line: "pursuant to § 50.90" mid-sentence is a cross-reference, not a heading.
_HEADING = re.compile(r"(?m)^[ \t]*(?:-[ \t]+)?§[ \t]*(\d+\.\d+[a-z]?)")
# Print furniture from the source CFR/FR PDFs (GPO footers/page numbers); never legal text.
_FURNITURE = re.compile(r"VerDate|Jkt|PO 00000|\bFrm\b|Sfmt|E:\\FR\\FM\\|^\s*\d+\s*$")
_TOP = re.compile(r"\([a-z]\)")
_DESIGNATOR = re.compile(r"^[ \t]*(?:-[ \t]+)?((?:\([A-Za-z0-9]+\))+)[ \t]*(.*)$")
_ROMAN = re.compile(r"[ivx]+")


def section_region(text: str, section: str = "50.59") -> str:
    heads = list(_HEADING.finditer(text))
    spans = []
    for i, h in enumerate(heads):
        if h.group(1) == section:
            nxt = next((n.start() for n in heads[i + 1:] if n.group(1) != section), len(text))
            spans.append(text[h.start():nxt])
    return "\n".join(spans)


def _depth(part: str, path: list[str]) -> int:
    inner = part[1:-1]
    if inner.isdigit():
        return 1
    if inner.isupper():
        return 3
    # ponytail: shape-based depth, so a renumbered-but-identical paragraph reads as removed + added,
    # and "(i)" at depth 0 is read as the letter i. Upgrade path: per-paragraph chunking
    # (CHUNKING_VERSION bump) if either case shows up in a real corpus.
    return 2 if _ROMAN.fullmatch(inner) and len(path) >= 2 else 0


def _region_lines(chunks: list[Chunk], section: str):
    """Yield (chunk, line) for body lines in `section`, tracking the current heading ACROSS chunks:
    a CFR-edition dump puts the heading in one chunk and continues it, heading-less, in the next."""
    # ponytail: a cross-reference that wraps onto the start of a line ("§ 50.90 ...") would be read
    # as a heading and switch the tracked section early. Needs a real layout signal to close.
    current = None
    for c in sorted(chunks, key=lambda c: (c.page, c.id)):
        for line in c.text.splitlines():
            if (line.startswith("Document: ") and " | revision: " in line) or _FURNITURE.search(line):
                continue
            h = _HEADING.match(line)
            # ponytail: a bare "§ 50.59" is a page running head; ignored entirely. If a source ever
            # used a bare running head to mark a real section change, the tracked section would
            # persist too long.
            if h and not line[h.end():].strip():
                continue
            if h:
                current = h.group(1)
            elif current == section:
                yield c, line


def paragraphs(chunks: list[Chunk], section: str = "50.59") -> dict[str, Paragraph]:
    out: dict[str, Paragraph] = {}
    path: list[str] = []
    key = None
    for c, line in _region_lines(chunks, section):
        m = _DESIGNATOR.match(line)
        body = line
        if m:
            for part in re.findall(r"\([A-Za-z0-9]+\)", m.group(1)):
                path = path[:_depth(part, path)] + [part]
            key, body = "".join(path), m.group(2)
            out.setdefault(key, Paragraph(key=key, text="", page=c.page,
                                          doc_id=c.doc_id, revision=c.revision))
        if key and body.strip():
            p = out[key]
            p.text = f"{p.text} {' '.join(body.split())}".strip()
    # CFR top-level paragraphs are always (a), (b)...; anything else is a continuation tail
    return {k: v for k, v in out.items() if _TOP.match(k)}


_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": '"', "’": '"'})


def normalize(text: str) -> str:
    text = text.replace("''", '"').replace("``", '"').translate(_QUOTES)
    return " ".join(text.casefold().split())
