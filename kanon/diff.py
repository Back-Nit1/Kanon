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


def paragraphs(chunks: list[Chunk], section: str = "50.59") -> dict[str, Paragraph]:
    out: dict[str, Paragraph] = {}
    path: list[str] = []
    key = None
    for c in sorted(chunks, key=lambda c: (c.page, c.id)):
        for line in section_region(c.text, section).splitlines():
            if _HEADING.match(line):
                continue
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
    return out


_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": '"', "’": '"'})


def normalize(text: str) -> str:
    text = text.replace("''", '"').replace("``", '"').translate(_QUOTES)
    return " ".join(text.casefold().split())
