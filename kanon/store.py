"""Layer 1 — bi-temporal authority store (SQLite).

Model taken from Graphiti (Zep, arXiv 2501.13956): two time axes, and outdated facts are
*invalidated* with a timestamp, never deleted. Graphiti itself was rejected as the
implementation: its edge extraction is LLM-driven (authority must be a lookup, not a model's
judgment), it needs Neo4j/FalkorDB (infra we don't need), and it's built for conversational
agent memory, not explicit supersession statements in formal documents.

  "I took Graphiti's bi-temporal model and rejected its extraction approach, because
   authority has to be a lookup."

Axes:
  valid time   — effective_from / effective_to on documents, valid_from on edges:
                 when something governed *in the world*.
  record time  — ingested_at / invalidated_at: when *we* learned or retracted it.
                 `known_at` replays what the store believed on a past date.

Nothing here sorts by issue_date. Issue date is recorded, never used for authority: a license
amendment can be issued after the date it took effect, so recency ranking gets it backwards.
"""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id          TEXT NOT NULL,   -- 'RG-1.187', 'MP-033.05', 'DPR-59-AMD-305'
  revision        TEXT NOT NULL,
  family          TEXT,            -- grouping key for cross-family supersession
  kind            TEXT NOT NULL,   -- regulation|reg_guide|tech_spec|license_amendment|operating_experience|rulemaking|other
  title           TEXT,
  -- valid time: when this governed in the world
  effective_from  DATE,
  effective_to    DATE,            -- NULL = still governing (or end unknown)
  -- record time: when we learned it
  issue_date      DATE,            -- when published; MAY be after effective_from
  ingested_at     TIMESTAMP NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('active','superseded','withdrawn')),
  accession       TEXT,
  source_url      TEXT,
  plants          TEXT NOT NULL DEFAULT '[]', -- JSON array; empty means not recorded
  units           TEXT NOT NULL DEFAULT '[]', -- JSON array; never infer a unit from a docket
  dockets         TEXT NOT NULL DEFAULT '[]', -- JSON array; plant or rulemaking dockets
  applicability_scope TEXT NOT NULL DEFAULT 'unknown'
                 CHECK (applicability_scope IN ('global','facility','unknown')),
  PRIMARY KEY (doc_id, revision)
);

CREATE TABLE IF NOT EXISTS supersession_edges (
  from_doc       TEXT NOT NULL,   -- the superseding document
  from_rev       TEXT NOT NULL,
  to_doc         TEXT NOT NULL,   -- the superseded document
  to_rev         TEXT NOT NULL,
  edge_type      TEXT NOT NULL CHECK (edge_type IN ('full','partial','withdrawn','cross_family')),
  scope_note     TEXT,            -- for 'partial': which portion
  valid_from     DATE,            -- when the supersession took effect (NULL = unknown)
  ingested_at    TIMESTAMP NOT NULL,
  invalidated_at TIMESTAMP,       -- record-time end: set when we retract an edge. Never DELETE.
  evidence       TEXT NOT NULL,   -- verbatim source text the edge was parsed from
  confidence     TEXT NOT NULL CHECK (confidence IN ('explicit','inferred','unresolved')),
  UNIQUE (from_doc, from_rev, to_doc, to_rev, edge_type, evidence)
);
CREATE INDEX IF NOT EXISTS edges_to ON supersession_edges (to_doc, to_rev);
CREATE INDEX IF NOT EXISTS edges_from ON supersession_edges (from_doc, from_rev);
"""

# Edge types that end the target's authority. 'partial' deliberately absent: under partial
# supersession the old document still governs whatever the new one didn't replace.
ENDS_AUTHORITY = {"full", "cross_family", "withdrawn"}


class DocumentVersion(BaseModel):
    doc_id: str
    revision: str
    family: str | None = None
    kind: str
    title: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    issue_date: date | None = None
    status: Literal["active", "superseded", "withdrawn"]
    accession: str | None = None
    source_url: str | None = None
    # Applicability is structured metadata, not free-form notes. Multiple units/dockets are
    # common, so these are lists rather than lossy comma-separated strings. Empty means unknown,
    # never "all". Global applicability must be explicitly recorded.
    plants: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    dockets: list[str] = Field(default_factory=list)
    applicability_scope: Literal["global", "facility", "unknown"] | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.doc_id, self.revision)

    def resolved_applicability_scope(self) -> Literal["global", "facility", "unknown"]:
        """Resolve scope from trusted structured fields; free-form text never makes it global."""
        if self.plants or self.units or self.dockets:
            return "facility"
        # `regulation` is a closed, loader-controlled kind and is searched as a governing rule
        # across facilities. Without this, adding a plant/unit filter would incorrectly hide
        # 10 CFR requirements from every facility-scoped question.
        if self.kind == "regulation":
            return "global"
        return self.applicability_scope or "unknown"


class Edge(BaseModel):
    from_doc: str
    from_rev: str
    to_doc: str
    to_rev: str
    edge_type: Literal["full", "partial", "withdrawn", "cross_family"]
    scope_note: str | None = None
    valid_from: date | None = None
    evidence: str
    confidence: Literal["explicit", "inferred", "unresolved"]

    def describe(self) -> str:
        when = self.valid_from.isoformat() if self.valid_from else "unknown date"
        return (f"{self.from_doc} Rev {self.from_rev} --{self.edge_type}--> "
                f"{self.to_doc} Rev {self.to_rev} (effective {when}, {self.confidence})")


class AuthorityResult(BaseModel):
    as_of: date
    authoritative: list[tuple[str, str]]   # >1 under partial / cross-family supersession
    chain: list[Edge]                      # every edge traversed, for traceability
    unresolved: list[Edge]                 # edges blocking a confident answer
    undated: list[tuple[str, str]]         # versions with no effective_from: can't place on timeline
    # versions recorded withdrawn with no end date and no dated ending edge: can't place on timeline
    withdrawn_undated: list[tuple[str, str]] = []

    @property
    def resolved(self) -> bool:
        return not self.unresolved and not self.undated and not self.withdrawn_undated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class AuthorityStore:
    def __init__(self, path: Path | str = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate_document_metadata()

    def _migrate_document_metadata(self) -> None:
        """SQLite's CREATE TABLE IF NOT EXISTS does not add new columns to an existing DB.

        Additive nullable/defaulted columns preserve existing authority stores. Applicability is
        deliberately unknown until structured metadata is provided or deterministic layout
        extraction succeeds during ingest.
        """
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(documents)")}
        additions = {
            "plants": "TEXT NOT NULL DEFAULT '[]'",
            "units": "TEXT NOT NULL DEFAULT '[]'",
            "dockets": "TEXT NOT NULL DEFAULT '[]'",
            "applicability_scope": "TEXT NOT NULL DEFAULT 'unknown'",
        }
        for name, declaration in additions.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE documents ADD COLUMN {name} {declaration}")
        schema = self.db.execute("SELECT sql FROM sqlite_master WHERE name='documents'").fetchone()[0]
        if "'generic'" in schema:
            # Migrate an early development schema without treating its inferred generic scope
            # as trusted global scope. There are no foreign keys to documents in this store.
            self.db.execute("ALTER TABLE documents RENAME TO documents_old_scope")
            self.db.execute(SCHEMA.split("CREATE TABLE IF NOT EXISTS supersession_edges")[0])
            self.db.execute("""INSERT INTO documents SELECT doc_id, revision, family, kind, title,
                effective_from, effective_to, issue_date, ingested_at, status, accession, source_url,
                plants, units, dockets, CASE WHEN applicability_scope='generic' THEN 'unknown'
                ELSE applicability_scope END FROM documents_old_scope""")
            self.db.execute("DROP TABLE documents_old_scope")
        self.db.commit()

    # ---- writes ---------------------------------------------------------------------------

    def upsert_document(self, d: DocumentVersion) -> None:
        # ponytail: overwrite on re-ingest, so record-time history of *document metadata* is
        # not kept (edges keep theirs). Add a documents_history table if metadata audits matter.
        self.db.execute(
            """INSERT INTO documents
               (doc_id, revision, family, kind, title, effective_from, effective_to, issue_date,
                ingested_at, status, accession, source_url, plants, units, dockets,
                applicability_scope)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (doc_id, revision) DO UPDATE SET
                 family=excluded.family, kind=excluded.kind, title=excluded.title,
                 effective_from=excluded.effective_from, effective_to=excluded.effective_to,
                 issue_date=excluded.issue_date, status=excluded.status,
                 accession=excluded.accession, source_url=excluded.source_url,
                 plants=excluded.plants, units=excluded.units, dockets=excluded.dockets,
                 applicability_scope=excluded.applicability_scope""",
            (d.doc_id, d.revision, d.family, d.kind, d.title,
             _iso(d.effective_from), _iso(d.effective_to), _iso(d.issue_date), _now(),
             d.status, d.accession, d.source_url, json.dumps(d.plants), json.dumps(d.units),
             json.dumps(d.dockets), d.resolved_applicability_scope()),
        )
        self.db.commit()

    def add_edge(self, e: Edge, ingested_at: str | None = None) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO supersession_edges
               (from_doc, from_rev, to_doc, to_rev, edge_type, scope_note, valid_from,
                ingested_at, invalidated_at, evidence, confidence)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,?)""",
            (e.from_doc, e.from_rev, e.to_doc, e.to_rev, e.edge_type, e.scope_note,
             _iso(e.valid_from), ingested_at or _now(), e.evidence, e.confidence),
        )
        self.db.commit()

    def invalidate_edge(self, e: Edge, at: str | None = None) -> None:
        """Retract an edge we recorded in error. Graphiti-style: stamp it, don't delete it, so a
        `known_at` replay still shows what we believed before the correction."""
        self.db.execute(
            """UPDATE supersession_edges SET invalidated_at=?
               WHERE from_doc=? AND from_rev=? AND to_doc=? AND to_rev=? AND edge_type=?
                 AND evidence=? AND invalidated_at IS NULL""",
            (at or _now(), e.from_doc, e.from_rev, e.to_doc, e.to_rev, e.edge_type, e.evidence),
        )
        self.db.commit()

    # ---- reads ----------------------------------------------------------------------------

    def get(self, doc_id: str, revision: str) -> DocumentVersion | None:
        row = self.db.execute("SELECT * FROM documents WHERE doc_id=? AND revision=?",
                              (doc_id, revision)).fetchone()
        return _doc(row) if row else None

    def all_documents(self) -> list[DocumentVersion]:
        return [_doc(r) for r in self.db.execute("SELECT * FROM documents")]

    def revision_history(self, doc_id: str) -> list[DocumentVersion]:
        """Newest first by *effective* date (never issue date). Undated revisions last."""
        docs = [_doc(r) for r in self.db.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,))]
        return sorted(docs, key=lambda d: d.effective_from or date.min, reverse=True)

    def edges_into(self, doc_id: str, revision: str, known_at: datetime | None = None) -> list[Edge]:
        """Edges whose target is (doc_id, revision), as recorded on `known_at` (default: now)."""
        rows = self.db.execute(
            "SELECT * FROM supersession_edges WHERE to_doc=? AND to_rev=?", (doc_id, revision))
        return [_edge(r) for r in rows if _live(r, known_at)]

    def edges_out_of(self, doc_id: str, revision: str, known_at: datetime | None = None) -> list[Edge]:
        """Edges whose source is (doc_id, revision): what it superseded, as recorded on `known_at`."""
        rows = self.db.execute(
            "SELECT * FROM supersession_edges WHERE from_doc=? AND from_rev=?", (doc_id, revision))
        return [_edge(r) for r in rows if _live(r, known_at)]

    def unresolved_into(self, doc_id: str, revision: str,
                        known_at: datetime | None = None) -> list[Edge]:
        """Edges into (doc_id, revision) that block a confident answer: undated or 'unresolved',
        and NOT corroborated by a dated edge between the same documents.

        Corroboration rule: NRC often states one supersession twice — dated in an index
        ("06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of
        RG 1.84") and undated in a notice. The undated copy adds no ambiguity. An undated
        statement with no dated twin (RG 1.1 -> RG 1.82) still blocks.
        """
        edges = self.edges_into(doc_id, revision, known_at)
        dated = [o for o in edges if o.valid_from and o.confidence != "unresolved"]
        return [e for e in edges
                if (e.confidence == "unresolved" or e.valid_from is None)
                and not any(o.from_doc == e.from_doc and e.from_rev in ("*", o.from_rev)
                            for o in dated)]

    # ---- the resolver ---------------------------------------------------------------------

    def governs(self, doc_id: str, revision: str, as_of: date,
                known_at: datetime | None = None) -> bool:
        """Did (doc_id, revision) govern on `as_of`? Pure lookup — no model involved."""
        d = self.get(doc_id, revision)
        if d is None or d.effective_from is None or d.effective_from > as_of:
            return False
        if d.effective_to is not None and as_of >= d.effective_to:
            return False
        if self._withdrawn_undated(d, known_at):
            return False
        for e in self.edges_into(doc_id, revision, known_at):
            if (e.edge_type in ENDS_AUTHORITY and e.confidence != "unresolved"
                    and e.valid_from is not None and e.valid_from <= as_of):
                return False
        return True

    def _withdrawn_undated(self, d: DocumentVersion, known_at: datetime | None) -> bool:
        """Recorded withdrawn, but nothing dates the end: we can't say when it stopped governing.
        Only 'withdrawn' — a 'superseded' revision legitimately governed in the past."""
        return (d.status == "withdrawn" and d.effective_to is None
                and not any(e.edge_type in ENDS_AUTHORITY and e.confidence != "unresolved"
                            and e.valid_from is not None
                            for e in self.edges_into(d.doc_id, d.revision, known_at)))

    def resolve_authority(self, doc_id: str, as_of: date,
                          known_at: datetime | None = None) -> AuthorityResult:
        """Documents governing `doc_id`'s subject matter on `as_of`.

        Walks supersession edges forward from every revision of `doc_id` (an edge points
        old -> new, we follow it from the superseded side to the superseding side), then keeps
        the visited versions that govern on `as_of`. Returns a *list*: partial and cross-family
        supersession mean one subject can be governed by several documents at once.
        """
        seeds = [d.key for d in self.revision_history(doc_id)]
        seen: set[tuple[str, str]] = set()
        frontier = list(seeds)
        chain: list[Edge] = []
        unresolved: list[Edge] = []
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            blocking = self.unresolved_into(*node, known_at=known_at)
            for e in self.edges_into(*node, known_at=known_at):
                if e.confidence == "unresolved" or e.valid_from is None:
                    # We can't place this edge on the timeline (or the source is ambiguous), so
                    # we can't say whether the target still governs. Surface it; don't guess.
                    if e in blocking:
                        unresolved.append(e)
                elif e.valid_from <= as_of:
                    chain.append(e)
                    frontier.append((e.from_doc, e.from_rev))
                # edges taking effect after as_of are ignored: they didn't exist yet in the world
            unresolved += self._conflicting_end_dates(node, as_of, known_at)
        authoritative = sorted(k for k in seen if self.governs(*k, as_of, known_at))
        undated = sorted(k for k in seen if (d := self.get(*k)) and d.effective_from is None)
        withdrawn_undated = sorted(k for k in seen if (d := self.get(*k))
                                   and self._withdrawn_undated(d, known_at))
        return AuthorityResult(as_of=as_of, authoritative=authoritative, chain=chain,
                               unresolved=unresolved, undated=undated,
                               withdrawn_undated=withdrawn_undated)

    def _conflicting_end_dates(self, node: tuple[str, str], as_of: date,
                               known_at: datetime | None) -> list[Edge]:
        """Two dated sources that disagree on when a version's authority ended.

        Real case: the NRC index says RG 1.85 Rev 31's guidance went into RG 1.84 Rev 32 in
        06/2003; the Federal Register withdrew RG 1.85 on 2004-06-18. Before the first date it
        governs, after the last it doesn't — in between, the record itself is ambiguous, so the
        later edges are returned as blocking rather than silently taking the earliest date.
        """
        ending = [e for e in self.edges_into(*node, known_at=known_at)
                  if e.edge_type in ENDS_AUTHORITY and e.confidence != "unresolved" and e.valid_from]
        dates = sorted({e.valid_from for e in ending})
        if len(dates) > 1 and dates[0] <= as_of < dates[-1]:
            return [e for e in ending if e.valid_from > as_of]
        return []

    def governing_set(self, as_of: date, known_at: datetime | None = None) -> set[tuple[str, str]]:
        """Every version governing on `as_of`. Used as the retrieval pre-filter in mode 'graph'."""
        # ponytail: O(documents x edges) Python scan. Fine for a few thousand docs; push into
        # a single SQL query if the corpus grows by an order of magnitude.
        return {d.key for d in self.all_documents() if self.governs(*d.key, as_of, known_at)}

    # ---- bulk load ------------------------------------------------------------------------

    def load_manifest(self, manifest: Path) -> int:
        """Load data/manifest.jsonl (the data-gathering contract) into `documents`."""
        n = 0
        for line in manifest.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                self.upsert_document(DocumentVersion.model_validate(row))
                n += 1
        return n


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _live(row: sqlite3.Row, known_at: datetime | None) -> bool:
    """Was this edge part of our record on `known_at`? (record-time axis)"""
    if known_at is None:
        return row["invalidated_at"] is None
    # Timestamps are stored as UTC ISO strings, so compare in the same form. Naive = UTC.
    if known_at.tzinfo is None:
        known_at = known_at.replace(tzinfo=timezone.utc)
    k = known_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    return row["ingested_at"] <= k and (row["invalidated_at"] is None or row["invalidated_at"] > k)


def _doc(r: sqlite3.Row) -> DocumentVersion:
    return DocumentVersion(
        doc_id=r["doc_id"], revision=r["revision"], family=r["family"], kind=r["kind"],
        title=r["title"], effective_from=_d(r["effective_from"]), effective_to=_d(r["effective_to"]),
        issue_date=_d(r["issue_date"]), status=r["status"], accession=r["accession"],
        source_url=r["source_url"], plants=json.loads(r["plants"] or "[]"),
        units=json.loads(r["units"] or "[]"), dockets=json.loads(r["dockets"] or "[]"),
        applicability_scope=("unknown" if r["applicability_scope"] == "generic"
                             else r["applicability_scope"]))


def _edge(r: sqlite3.Row) -> Edge:
    return Edge(from_doc=r["from_doc"], from_rev=r["from_rev"], to_doc=r["to_doc"],
                to_rev=r["to_rev"], edge_type=r["edge_type"], scope_note=r["scope_note"],
                valid_from=_d(r["valid_from"]), evidence=r["evidence"], confidence=r["confidence"])
