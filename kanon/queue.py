"""Ingest job queue + chunk metadata sidecar, both in Postgres.

One row per (document revision, file content) in `ingest_jobs`. Workers claim rows with
`FOR UPDATE SKIP LOCKED`, so N workers on N machines never take the same job and never block
each other. Progress (`done_batches`) commits in the same transaction as the chunk metadata it
describes, so a crashed worker's job resumes at the first batch whose metadata isn't recorded.

Rejected: Celery/RQ + Redis — a second stateful service, and delivery is at-least-once anyway,
  so we would still need this table for idempotency and resume. Postgres gives claim semantics
  and transactional progress next to the metadata it guards.
Rejected: Kafka — a partitioned log for high-throughput streams. Regulatory ingest is thousands
  of documents, not millions of events per second.

Idempotency key: `doc_id@revision:sha256(file)[:16]`.
  - Re-enqueueing the same file is a no-op (primary-key conflict).
  - A changed file is a new key, so a new job; it purges the revision's old chunks first.
  - Chunk ids are deterministic (`doc_id@rev#0007`) and every write is an upsert, so redoing a
    batch after a crash overwrites instead of duplicating, in Chroma and here.

Failure: every exception is an attempt. After MAX_ATTEMPTS the job is `dead` (the dead letter
queue), its partial chunks are purged so a half-ingested document is never searchable, and
each attempt's stage, error and a diagnosis stay on the row for `kanon dlq`.
"""

import hashlib
import json
import math
import os
import socket
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kanon.checks import PipelineIntegrityError, check_chunks
from kanon.ingest import Chunk, chunk_document

MAX_ATTEMPTS = 3
BATCH = 64                  # chunks per embed call and per progress checkpoint
LEASE = "15 minutes"        # must exceed the slowest single parse; heartbeat renews it per batch
RETRY_BASE_SECONDS = 10     # backoff 10s, 20s: gives a rate-limited embedding API time to recover

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
  idem_key       text PRIMARY KEY,
  doc_id         text NOT NULL,
  revision       text NOT NULL,
  local_path     text NOT NULL,
  page_range     int[],
  paginated      boolean NOT NULL,
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'dead')),
  attempts       int NOT NULL DEFAULT 0,
  done_batches   int NOT NULL DEFAULT 0,
  n_chunks       int,
  run_after      timestamptz NOT NULL DEFAULT now(),
  leased_until   timestamptz,
  worker         text,
  last_stage     text,
  errors         jsonb NOT NULL DEFAULT '[]',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
  id             text PRIMARY KEY,
  idem_key       text NOT NULL REFERENCES ingest_jobs ON DELETE CASCADE,
  doc_id         text NOT NULL,
  revision       text NOT NULL,
  doc_key        text NOT NULL,
  kind           text NOT NULL,
  status         text NOT NULL,
  effective_from date,
  effective_to   date,
  issue_date     date,
  section        text NOT NULL,
  page           int NOT NULL CHECK (page >= 1),
  paginated      boolean NOT NULL,
  accession      text NOT NULL,
  source_url     text NOT NULL,
  n_chars        int NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc_key ON chunks (doc_key);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks (status, kind, effective_from, effective_to);
CREATE OR REPLACE VIEW dead_letter_queue AS
  SELECT idem_key, doc_id, revision, local_path, attempts, last_stage, errors, updated_at
  FROM ingest_jobs WHERE status = 'dead';
"""

# A job whose worker stopped heartbeating (crash, OOM kill, hang). Appended to `errors` when the
# job is reclaimed or declared dead, so a crash counts as an attempt like any exception does.
_LEASE_LOST = """errors || jsonb_build_array(jsonb_build_object(
    'attempt', attempts, 'stage', last_stage, 'type', 'LeaseExpired', 'at', now(),
    'message', 'worker ' || coalesce(worker, '?') || ' stopped heartbeating (crash, kill or hang)',
    'diagnosis', 'worker died mid-job; if this repeats on the same file, the parser is crashing on it'))"""

_CLAIM = f"""
UPDATE ingest_jobs SET
  errors = CASE WHEN status = 'running' THEN {_LEASE_LOST} ELSE errors END,
  status = 'running', attempts = attempts + 1, worker = %(worker)s,
  leased_until = now() + %(lease)s::interval, updated_at = now()
WHERE idem_key = (
  SELECT idem_key FROM ingest_jobs
  WHERE (status = 'queued' AND run_after <= now())
     OR (status = 'running' AND leased_until < now() AND attempts < %(max)s)
  ORDER BY created_at, idem_key
  FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *"""

# Crashed on its last allowed attempt: nothing will reclaim it, so it goes to the DLQ here.
_REAP = f"""
UPDATE ingest_jobs SET errors = {_LEASE_LOST}, status = 'dead', leased_until = NULL, updated_at = now()
WHERE status = 'running' AND leased_until < now() AND attempts >= %(max)s
RETURNING doc_id, revision"""


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    conn.execute(SCHEMA)
    return conn


def idem_key(doc_id: str, revision: str, path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "missing"
    return f"{doc_id}@{revision}:{digest}"


def enqueue_manifest(conn: psycopg.Connection, data_dir: Path) -> int:
    """Returns how many new jobs were queued; unchanged files already have a job."""
    added = 0
    for line in (data_dir / "manifest.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = idem_key(row["doc_id"], row["revision"], data_dir / row["local_path"])
        added += conn.execute(
            "INSERT INTO ingest_jobs (idem_key, doc_id, revision, local_path, page_range, paginated)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (idem_key) DO NOTHING",
            (key, row["doc_id"], row["revision"], row["local_path"], row.get("page_range"),
             row.get("paginated", True))).rowcount
    return added


def claim(conn: psycopg.Connection, worker: str) -> dict | None:
    for r in conn.execute(_REAP, {"max": MAX_ATTEMPTS}).fetchall():
        print(f"  DEAD  {r['doc_id']} Rev {r['revision']}: worker lost on final attempt")
    return conn.execute(_CLAIM, {"worker": worker, "lease": LEASE, "max": MAX_ATTEMPTS}).fetchone()


def process(conn: psycopg.Connection, job: dict, data_dir: Path, store, index, converter, chunker) -> None:
    """Run one claimed job to `done`, or record the failure (requeue with backoff, or dead)."""
    path, stage = data_dir / job["local_path"], "parse"
    doc_key = f"{job['doc_id']}@{job['revision']}"
    try:
        doc = store.get(job["doc_id"], job["revision"])
        if doc is None:
            raise LookupError(f"{doc_key} not in authority store")
        pr = tuple(job["page_range"]) if job["page_range"] else None
        # ponytail: resume assumes chunking is deterministic for a given file hash. Holds while
        # the Docling version is pinned by uv.lock; add the version to idem_key if that changes.
        chunks = chunk_document(path, doc, job["paginated"], converter, chunker, page_range=pr)
        stage = "validate"
        check_chunks(chunks)                       # fail before paying for embeddings
        if job["done_batches"] == 0:               # fresh start: drop any older file's chunks
            stage = "purge"
            _purge(conn, index, doc_key)
        for b in range(job["done_batches"], math.ceil(len(chunks) / BATCH)):
            part = chunks[b * BATCH:(b + 1) * BATCH]
            stage = "embed"
            index.add(part)                        # embed + upsert by chunk id: safe to redo
            stage = "record"
            with conn.transaction():               # metadata and progress commit together
                _record(conn, job["idem_key"], part)
                conn.execute("UPDATE ingest_jobs SET done_batches = %s, last_stage = 'record',"
                             " leased_until = now() + %s::interval, updated_at = now()"
                             " WHERE idem_key = %s", (b + 1, LEASE, job["idem_key"]))
        conn.execute("UPDATE ingest_jobs SET status = 'done', n_chunks = %s, leased_until = NULL,"
                     " updated_at = now() WHERE idem_key = %s", (len(chunks), job["idem_key"]))
        print(f"  done  {doc_key}: {len(chunks)} chunks")
    except Exception as exc:
        _fail(conn, index, job, path, stage, exc)


def run_worker(conn, data_dir: Path, store, index, converter, chunker, poll: float = 2.0) -> None:
    """Work until no job is queued, running elsewhere, or waiting out a retry backoff."""
    worker = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        job = claim(conn, worker)
        if job:
            process(conn, job, data_dir, store, index, converter, chunker)
        elif conn.execute("SELECT 1 FROM ingest_jobs WHERE status IN ('queued', 'running')").fetchone():
            time.sleep(poll)
        else:
            return


def counts(conn) -> dict[str, int]:
    return {r["status"]: r["n"] for r in
            conn.execute("SELECT status, count(*) AS n FROM ingest_jobs GROUP BY status")}


def chunk_ids(conn) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM chunks")}


def requeue(conn, key: str) -> bool:
    """Send a dead job back for a fresh 3 attempts (after fixing the file). History is kept."""
    return bool(conn.execute(
        "UPDATE ingest_jobs SET status = 'queued', attempts = 0, done_batches = 0, run_after = now(),"
        " updated_at = now() WHERE idem_key = %s AND status = 'dead'", (key,)).rowcount)


def dead_letter_report(conn) -> str:
    rows = conn.execute("SELECT * FROM dead_letter_queue ORDER BY updated_at").fetchall()
    if not rows:
        return "dead letter queue is empty"
    out = [f"{len(rows)} document(s) in the dead letter queue\n"]
    for r in rows:
        errs = r["errors"]
        same = len({(e["type"], e["message"]) for e in errs}) == 1
        out += [f"{r['doc_id']} Rev {r['revision']}   {r['local_path']}",
                f"  key:       {r['idem_key']}",
                f"  diagnosis: {errs[-1]['diagnosis']}",
                f"  pattern:   {'same error every attempt: the file, not the system' if same else 'errors differ across attempts: likely transient/infra'}"]
        for e in errs:
            out.append(f"  attempt {e['attempt']} [{e['stage']}] {e['type']}: {e['message'][:200]}")
        out.append(f"  requeue after fixing: kanon dlq --requeue '{r['idem_key']}'\n")
    return "\n".join(out)


def diagnose(path: Path, exc: BaseException) -> str:
    """Name the likely real cause, so a reviewer starts from the file, not a stack trace."""
    if not path.exists():
        return "file missing: re-fetch it or fix local_path in data/manifest.jsonl"
    if path.stat().st_size == 0:
        return "empty file: the download was truncated to 0 bytes"
    if isinstance(exc, LookupError):
        return "document not in the authority store: run `kanon load` before ingest"
    if path.suffix == ".pdf":
        raw = path.read_bytes()
        if b"%PDF-" not in raw[:1024]:
            return f"not a PDF (no %PDF- header; starts {raw[:40]!r}): often an HTML error page saved as .pdf"
        if b"%%EOF" not in raw[-2048:]:
            return "truncated PDF (no %%EOF trailer): the download was cut off, re-fetch it"
        if b"/Encrypt" in raw:
            return "encrypted PDF: decrypt it before ingest"
    if isinstance(exc, PipelineIntegrityError) and "zero chunks" in str(exc):
        return "no extractable text: probably a scanned PDF with no text layer (OCR is off by design)"
    if type(exc).__name__ in ("RateLimitError", "APIConnectionError", "APITimeoutError",
                              "InternalServerError", "OperationalError"):
        return "transient service error (embedding API / database): requeue once it recovers"
    return "unclassified: read the traceback stored on the row"


def _record(conn, key: str, part: list[Chunk]) -> None:
    conn.cursor().executemany(
        "INSERT INTO chunks (id, idem_key, doc_id, revision, doc_key, kind, status, effective_from,"
        " effective_to, issue_date, section, page, paginated, accession, source_url, n_chars)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET idem_key = EXCLUDED.idem_key, status = EXCLUDED.status,"
        " section = EXCLUDED.section, page = EXCLUDED.page, n_chars = EXCLUDED.n_chars",
        [(c.id, key, c.doc_id, c.revision, c.doc_key, c.kind, c.status, c.effective_from or None,
          c.effective_to or None, c.issue_date or None, c.section, c.page, c.paginated,
          c.accession, c.source_url, len(c.text)) for c in part])


def _purge(conn, index, doc_key: str) -> None:
    index.remove_doc(doc_key)
    conn.execute("DELETE FROM chunks WHERE doc_key = %s", (doc_key,))


def _fail(conn, index, job: dict, path: Path, stage: str, exc: Exception) -> None:
    dead = job["attempts"] >= MAX_ATTEMPTS
    entry = {"attempt": job["attempts"], "stage": stage, "type": type(exc).__name__,
             "message": str(exc), "diagnosis": diagnose(path, exc),
             "traceback": traceback.format_exc(limit=8),
             "at": datetime.now(timezone.utc).isoformat()}
    if dead:
        # Never leave a half-ingested document searchable. A requeue restarts from batch 0.
        _purge(conn, index, f"{job['doc_id']}@{job['revision']}")
    conn.execute(
        "UPDATE ingest_jobs SET status = %s, errors = errors || %s, last_stage = %s,"
        " done_batches = CASE WHEN %s THEN 0 ELSE done_batches END,"
        " run_after = now() + %s * interval '1 second', leased_until = NULL, updated_at = now()"
        " WHERE idem_key = %s",
        ("dead" if dead else "queued", Jsonb([entry]), stage, dead,
         RETRY_BASE_SECONDS * 2 ** (job["attempts"] - 1), job["idem_key"]))
    print(f"  {'DEAD ' if dead else 'retry'} {job['doc_id']} Rev {job['revision']}"
          f" attempt {job['attempts']}/{MAX_ATTEMPTS} [{stage}] {type(exc).__name__}: {str(exc)[:120]}")
