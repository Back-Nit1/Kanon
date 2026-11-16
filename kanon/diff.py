"""Deterministic CFR paragraph extraction from chunk text already in the index. No model, no I/O.

Docling `section` metadata cannot key paragraphs (the 1996 CFR-edition chunks are labelled with the
whole-edition title), so designators like (a)(2) are recovered from the text itself, scoped to one
CFR section so §50.58/§50.60/§50.61 text sharing a chunk is never attributed to §50.59.
"""

import re
from datetime import date

from pydantic import BaseModel

from kanon.ingest import Chunk
from kanon.retrieval import is_record
from kanon.store import Edge


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


class ParagraphDelta(BaseModel):
    key: str
    old: Paragraph
    new: Paragraph


class DiffResult(BaseModel):
    doc_id: str
    from_as_of: date
    to_as_of: date
    from_revisions: list[str]
    to_revisions: list[str]
    gone: list[Paragraph] = []
    added: list[Paragraph] = []
    changed: list[ParagraphDelta] = []
    ended_by: list[Edge] = []
    unresolved: list[Edge] = []
    notes: list[str] = []
    kinds: dict[str, str] = {}      # "doc@rev" -> stored kind, so format_diff needs no store


def diff_paragraphs(old: dict[str, Paragraph], new: dict[str, Paragraph]):
    gone = [old[k] for k in old if k not in new]
    added = [new[k] for k in new if k not in old]
    changed = [ParagraphDelta(key=k, old=old[k], new=new[k]) for k in old
               if k in new and normalize(old[k].text) != normalize(new[k].text)]
    return gone, added, changed


def _side(index, revs, doc_id, section):
    out: dict[str, Paragraph] = {}
    for rev in revs:
        out.update(paragraphs(index.chunks_for(f"{doc_id}@{rev}"), section))
    return out


def diff_as_of(index, store, doc_id: str, from_as_of: date, to_as_of: date,
               section: str | None = None) -> DiffResult:
    sides = [store.resolve_authority(doc_id, d) for d in (from_as_of, to_as_of)]
    r = DiffResult(doc_id=doc_id, from_as_of=from_as_of, to_as_of=to_as_of,
                   from_revisions=[k[1] for k in sides[0].authoritative],
                   to_revisions=[k[1] for k in sides[1].authoritative])
    for res in sides:
        for k in res.authoritative:
            if (d := store.get(*k)):
                r.kinds[f"{k[0]}@{k[1]}"] = d.kind
    r.unresolved = [e for res in sides for e in res.unresolved]
    if r.unresolved:
        r.notes.append("authority is unresolved on at least one date; not diffing an ambiguous record")
        return r
    for res, d in zip(sides, (from_as_of, to_as_of)):
        if not res.authoritative:
            r.notes.append(f"nothing governed on {d}")
            history = store.revision_history(doc_id)
            if history:
                first = history[-1]
                r.notes.append(f"earliest recorded version: {first.doc_id} Rev {first.revision}, "
                               f"effective {first.effective_from or 'unknown'}")
    if not (sides[0].authoritative and sides[1].authoritative):
        return r
    have = [f"{doc_id}@{v}" for v in r.from_revisions + r.to_revisions]
    missing = [k for k in have if not index.chunks_for(k)]
    if missing:
        r.notes += [f"no text indexed for {k}" for k in missing]
        return r
    if section is None:
        m = re.fullmatch(r"10CFR(\d+\.\d+[a-z]?)", doc_id)
        if not m:
            r.notes.append(f"no CFR section derivable from {doc_id}; paragraph extraction skipped")
            return r
        section = m.group(1)
    old = _side(index, r.from_revisions, doc_id, section)
    new = _side(index, r.to_revisions, doc_id, section)
    r.gone, r.added, r.changed = diff_paragraphs(old, new)
    for rev in r.from_revisions:
        r.ended_by += [e for e in store.edges_into(doc_id, rev)
                       if e.edge_type != "partial" and e.valid_from
                       and from_as_of < e.valid_from <= to_as_of]
    return r


def _label(r: DiffResult, doc: str, rev: str) -> str:
    kind = r.kinds.get(f"{doc}@{rev}")
    role = "unknown kind" if kind is None else ("record" if is_record(kind) else "requirement")
    return f"{doc}@{rev} ({role})"


def format_diff(result: DiffResult, quote_chars: int = 160) -> str:
    r = result
    q = lambda t: f"\u201c{t[:quote_chars]}{'...' if len(t) > quote_chars else ''}\u201d"
    ref = lambda x: f"{x.doc_id}@{x.revision} p.{x.page}"
    lines = [f"Diff of {r.doc_id}: what governed on {r.from_as_of} vs {r.to_as_of}"]
    for tag, d, revs in (("FROM", r.from_as_of, r.from_revisions), ("TO  ", r.to_as_of, r.to_revisions)):
        gov = ", ".join(_label(r, r.doc_id, v) for v in revs) or "NOTHING"
        lines.append(f"  {tag} {d}: {gov}")
    lines += [f"  NOTE {n}" for n in r.notes]
    lines += [f"  UNRESOLVED {e.describe()}\n             {q(e.evidence)}" for e in r.unresolved]
    for e in r.ended_by:
        lines.append(f"  ENDED BY {e.describe()}")
    for p in r.gone:
        lines.append(f"\nGONE {p.key}  [{ref(p)}]\n  {q(p.text)}")
        lines += [f"  ended by: {e.describe()}" for e in r.ended_by]
    for d in r.changed:
        lines.append(f"\nCHANGED {d.key}\n  was [{ref(d.old)}]: {q(d.old.text)}"
                     f"\n  now [{ref(d.new)}]: {q(d.new.text)}")
    for p in r.added:
        lines.append(f"\nNEW {p.key}  [{ref(p)}]\n  {q(p.text)}")
    if r.gone or r.added:
        # A reader seeing "GONE (c)" must not conclude the subject matter was dropped: in the
        # 1996 -> 2025 diff, (c) was restructured into (c)(1)/(c)(2), which reads as GONE + NEW.
        lines += ["", "GONE/NEW are designator-level. A paragraph renumbered or restructured "
                  "between revisions reads as GONE plus NEW, not CHANGED, so compare the quoted "
                  "text before concluding a requirement disappeared."]
    return "\n".join(lines)
