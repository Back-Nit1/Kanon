"""Parse verbatim supersession statements into typed edges. Deterministic: regex + rules.

Input rows are exact quotes from NRC documents and index pages (data/labels/). We never ask a
model what a sentence means: an LLM reading "incorporated into Revision 32 of RG 1.84" is right
99% of the time, and the 1% is a licensing error nobody can audit. A regex that fails, fails
loudly (confidence='unresolved').

The patterns are calibrated on the real phrasing in the corpus, which is not the phrasing you'd
guess. NRC rarely writes "supersedes"; it writes:
  - "guidance ... has been updated and incorporated into Revision 1 of Regulatory Guide 1.78"
  - "redundant due to the inclusion of its subject matter in Rev. 3 of RG 1.160"
  - "(Withdrawn -- See 80 FR 24293, 04/30/2015)"
  - CFR amendment instructions: "In § 50.59, paragraphs (b), (d)(2), and (d)(3) are revised"

Confidence ladder:
  explicit   — text names both sides and an exact date
  inferred   — a deterministic rule filled a gap: revision pinned from effective dates, or a
               month-only date ("06/2003") taken as the 1st of that month
  unresolved — no date, or no parseable target. The gate abstains on these and names them.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

from kanon.store import AuthorityStore, DocumentVersion, Edge

UNKNOWN = "UNKNOWN"   # placeholder doc when a supersession verb appears with no parseable target
ANY_REV = "*"         # text named a document but not its revision; pinned by resolve_wildcards

# "Regulatory Guide 1.187, Revision 2" / "RG 1.187 Rev. 2" / "Revision 32 of Regulatory Guide 1.84"
GUIDE_REF = re.compile(
    r"(?:(?:Revision|Rev\.?)\s*(?P<rev1>\d+)\s+(?:of|to)\s+)?"
    r"(?:Regulatory\s+Guide|Reg\.?\s*Guide|RG)\s*(?P<num>\d+\.\d+)"
    r"(?:\s*,?\s*\(?(?:Revision|Rev\.?)\s*(?P<rev2>\d+)\)?)?",
    re.IGNORECASE)
BARE_REV = re.compile(r"\b(?:Revision|Rev\.?)\s*(?P<rev>\d+)\b", re.IGNORECASE)

# Subject is the OLD side; what follows the marker is the NEW side.
REPLACED_BY = re.compile(
    r"\b(?:incorporated\s+(?:into|in)|(?:superseded|replaced)\s+by|already\s+contained\s+in"
    r"|inclusion\s+of\s+its\s+subject\s+matter\s+in)\b", re.IGNORECASE)
# Subject is the NEW side; what follows is the OLD side. Active voice: "Revision 1 incorporates
# guidance from withdrawn Regulatory Guide 1.95".
SUPERSEDES = re.compile(r"\b(?:supersedes?|superseding|replaces?|incorporates\s+(?:guidance\s+)?from)\b",
                        re.IGNORECASE)
WITHDRAWN = re.compile(r"\bwithdr[ae]wn?\b|\bwithdrawing\b|\bwithdraws\b|\bwithdrawal\b", re.IGNORECASE)
# CFR amendment instructions. Partial at the *instruction* level, but the documents we store are
# compiled section texts, so the new compiled version fully replaces the prior compiled version;
# which paragraphs changed goes in scope_note.
AMENDS = re.compile(
    r"\bparagraphs?\s+\([a-z0-9]+\).*?\b(?:are|is)\s+(?:added|revised|amended)\b"
    r"|\b(?:revise|amend)\s+paragraph\b|\binsert\s+the\s+words\b|\bremoving\s+the\s+text\b",
    re.IGNORECASE | re.DOTALL)
PAGE_SWAP = re.compile(r"\bremove\b.*\binsert\b", re.IGNORECASE | re.DOTALL)
PARTIAL = re.compile(r"\b(?:portions?\s+of|in\s+part|partially)\b", re.IGNORECASE)
TAKES_EFFECT = re.compile(r"\btakes?\s+effect\s+on\b", re.IGNORECASE)

MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
DATE_LONG = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")
DATE_MDY = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
DATE_MY = re.compile(r"\b(\d{2})/(\d{4})\b")


def guide_id(num: str) -> str:
    return f"RG-{num}"


def parse_evidence(subject_doc: str, subject_rev: str, text: str,
                   stated_date: date | None = None) -> list[Edge]:
    """One verbatim statement -> zero or more edges. Rule order is the whole algorithm:

      1. CFR amendment instruction  -> new compiled version replaces the prior one ('full')
      2. "incorporated into X" etc. -> subject is OLD, X is NEW  (cross_family if X is another doc)
      3. "supersedes X"             -> subject is NEW, X is OLD
      4. withdrawal (nothing above) -> 'withdrawn' onto the subject
      5. "remove page / insert page" with no named target -> 'unresolved'
    Withdrawal is checked last because NRC withdrawal notices usually *also* say where the
    guidance went ("withdrawing RG 1.1 because ... incorporated into RG 1.82"), and the
    successor is the more informative edge.
    """
    when, exact = (stated_date, True) if stated_date else _date_in(text)
    me = (subject_doc, subject_rev)

    if AMENDS.search(text):
        return [_edge(new=me, old=(subject_doc, ANY_REV), when=when, exact=exact, text=text,
                      scope=text[:240])]

    if m := REPLACED_BY.search(text):
        refs = [r for r in _refs(text[m.end():], subject_doc) if r != me]
        if not refs:
            return [_unresolved(UNKNOWN, ANY_REV, *me, text, when)]
        return [_edge(new=r, old=me, when=when, exact=exact, text=text) for r in refs]

    if m := SUPERSEDES.search(text):
        refs = [r for r in _refs(text[m.end():], subject_doc) if r != me]
        if not refs:
            return [_unresolved(*me, UNKNOWN, ANY_REV, text, when)]
        return [_edge(new=me, old=r, when=when, exact=exact, text=text) for r in refs]

    if WITHDRAWN.search(text):
        if any(r[0] != subject_doc for r in _refs(text, subject_doc)):
            # The sentence withdraws some *other* named guide; it says nothing certain about the
            # subject. Never turn that into a self-withdrawal.
            return [_unresolved(*me, UNKNOWN, ANY_REV, text, when,
                                note="withdrawal statement names a different document")]
        # No successor document: the edge points from the version to itself, meaning
        # "this version's authority ends at valid_from".
        return [Edge(from_doc=subject_doc, from_rev=subject_rev, to_doc=subject_doc,
                     to_rev=subject_rev, edge_type="withdrawn", valid_from=when, evidence=text,
                     confidence=_confidence(when, exact))]

    if PAGE_SWAP.search(text):
        return [_unresolved(*me, UNKNOWN, ANY_REV, text, when,
                            note="page replacement without a named target document")]
    return []


def _refs(text: str, subject_doc: str) -> list[tuple[str, str]]:
    """Document references in `text`, in order. A bare 'Revision N' means the subject doc."""
    refs = [(guide_id(m["num"]), m["rev1"] or m["rev2"] or ANY_REV) for m in GUIDE_REF.finditer(text)]
    if not refs:
        refs = [(subject_doc, m["rev"]) for m in BARE_REV.finditer(text)]
    return list(dict.fromkeys(refs))   # de-dupe, keep order


def _confidence(when: date | None, exact: bool) -> str:
    # no date = can't place it on the timeline = can't use it to decide authority
    return "unresolved" if when is None else ("explicit" if exact else "inferred")


def _edge(new, old, when, exact, text, scope=None) -> Edge:
    partial = bool(PARTIAL.search(text))
    edge_type = "partial" if partial else ("cross_family" if new[0] != old[0] else "full")
    return Edge(from_doc=new[0], from_rev=new[1], to_doc=old[0], to_rev=old[1],
                edge_type=edge_type, valid_from=when, evidence=text,
                confidence=_confidence(when, exact),
                scope_note=scope or (text[:240] if partial else None))


def _unresolved(fd, fr, td, tr, text, when, note="supersession verb but no parseable target") -> Edge:
    return Edge(from_doc=fd, from_rev=fr, to_doc=td, to_rev=tr, edge_type="full",
                valid_from=when, evidence=text, confidence="unresolved", scope_note=note)


def _date_in(text: str) -> tuple[date | None, bool]:
    """First date in the text -> (date, exact?). Month-only dates are not exact."""
    if m := DATE_LONG.search(text):
        return datetime.strptime(" ".join(m.groups()), "%B %d %Y").date(), True
    if m := DATE_MDY.search(text):
        return date(int(m[3]), int(m[1]), int(m[2])), True
    if m := DATE_MY.search(text):
        return date(int(m[2]), int(m[1]), 1), False
    return None, False


# ---- wildcard resolution ------------------------------------------------------------------

def resolve_wildcards(edge: Edge, store: AuthorityStore) -> Edge:
    """Pin revision '*' using recorded effective dates; pinned edges are at best 'inferred'.

    Old side: the revision in effect just BEFORE valid_from (it's what got replaced).
    New side: the revision in effect ON valid_from (e.g. "incorporated into RG 1.183" in 2016
              means the RG 1.183 revision current in 2016, not the next one issued).
    """
    if ANY_REV not in (edge.from_rev, edge.to_rev) or edge.confidence == "unresolved":
        return edge
    e = edge.model_copy()
    for side, strictly_before in (("to", True), ("from", False)):
        doc, rev = getattr(e, f"{side}_doc"), getattr(e, f"{side}_rev")
        if rev != ANY_REV:
            continue
        pinned = _in_effect(store, doc, e.valid_from, strictly_before)
        if pinned is None:
            return e.model_copy(update={"confidence": "unresolved",
                                        "scope_note": f"cannot pin revision of {doc} on {e.valid_from}"})
        setattr(e, f"{side}_rev", pinned)
        e.confidence = "inferred"
    return e


def _in_effect(store: AuthorityStore, doc: str, on: date, strictly_before: bool) -> str | None:
    for d in store.revision_history(doc):   # newest effective first
        if d.effective_from and (d.effective_from < on if strictly_before else d.effective_from <= on):
            return d.revision
    return None


def infer_revision_edges(store: AuthorityStore) -> list[Edge]:
    """Same-document succession: Rev N+1 fully supersedes Rev N, ordered by effective_from.

    Only where the source itself marks the older version superseded/withdrawn — we don't invent
    succession for versions the source calls active (that's how partial cases stay visible).
    """
    edges = []
    by_doc: dict[str, list[DocumentVersion]] = {}
    for d in store.all_documents():
        by_doc.setdefault(d.doc_id, []).append(d)
    for doc_id, versions in by_doc.items():
        dated = sorted((v for v in versions if v.effective_from), key=lambda v: v.effective_from)
        for old, new in zip(dated, dated[1:]):
            if old.status != "active":
                edges.append(Edge(
                    from_doc=doc_id, from_rev=new.revision, to_doc=doc_id, to_rev=old.revision,
                    edge_type="full", valid_from=new.effective_from, confidence="inferred",
                    evidence=(f"[derived] source records {doc_id} Rev {old.revision} as "
                              f"{old.status}; next revision Rev {new.revision} effective "
                              f"{new.effective_from}")))
    return edges


# ---- loaders ------------------------------------------------------------------------------

def load_rg_index(store: AuthorityStore, index_file: Path) -> int:
    """NRC Regulatory Guide division index -> metadata-only document rows (no text chunks).

    These rows exist so edges like "RG 1.1 incorporated into RG 1.82" have real nodes to point
    at. Conventions, each stated so a reviewer can challenge it:
      - 'W' rows are withdrawal markers and 'DG' rows are unissued draft guides: not versions.
      - The index gives month-only dates; effective_from = issue_date = 1st of that month.
        Where a primary source states the real effective date ("takes effect on July 7, 2020"),
        load_labels overrides it.
      - Newest numbered revision: 'withdrawn' if the guide's status says so, else 'active'.
        Every older revision: 'superseded'.
    """
    rows = [json.loads(line) for line in index_file.read_text().splitlines() if line.strip()]
    by_guide: dict[str, list[dict]] = {}
    for r in rows:
        if r["revision"].isdigit():
            by_guide.setdefault(r["rg_number"], []).append(r)
    n = 0
    for num, revs in by_guide.items():
        revs.sort(key=lambda r: int(r["revision"]))
        for r in revs:
            when = date.fromisoformat(r["issue_date"] + "-01") if r.get("issue_date") else None
            newest = r is revs[-1]
            withdrawn = "withdrawn" in (r.get("status_raw") or "").lower()
            store.upsert_document(DocumentVersion(
                doc_id=guide_id(num), revision=r["revision"], family=guide_id(num),
                kind="reg_guide", title=r.get("title"), effective_from=when, issue_date=when,
                status=("withdrawn" if withdrawn else "active") if newest else "superseded",
                source_url=r.get("source_url")))
            n += 1
    return n


def load_labels(store: AuthorityStore, evidence_file: Path) -> dict[str, int]:
    """Parse every evidence row into edges and store them. Returns counts by confidence."""
    counts = {"explicit": 0, "inferred": 0, "unresolved": 0}
    rows = [json.loads(line) for line in evidence_file.read_text().splitlines() if line.strip()]
    # Effective-date statements first: they correct document dates that wildcard pinning and
    # revision inference depend on. Narrow on purpose ("takes effect on") — "Effective April 30,
    # 2015, the NRC withdraws RG 1.1" is a withdrawal date, not a document's effective date.
    for row in rows:
        when, exact = _date_in(row["evidence"])
        doc = store.get(row["subject_doc"], row["subject_rev"])
        if TAKES_EFFECT.search(row["evidence"]) and exact and doc:
            store.upsert_document(doc.model_copy(update={"effective_from": when}))
    for row in rows:
        stated = date.fromisoformat(row["stated_date"]) if row.get("stated_date") else None
        for edge in parse_evidence(row["subject_doc"], row["subject_rev"], row["evidence"], stated):
            edge = resolve_wildcards(edge, store)
            store.add_edge(edge)
            counts[edge.confidence] += 1
    for edge in infer_revision_edges(store):
        store.add_edge(edge)
        counts["inferred"] += 1
    return counts
