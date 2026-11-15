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

Idempotency is a database-level composite unique key over
`(doc_id, revision, authority_status, source_sha256, chunking_version)`.
  - Re-enqueueing the same file is a no-op (primary-key conflict).
  - A changed file or chunker is a new key, so a new job; it purges the revision's old chunks
    first, so a shorter new version leaves no stale tail.
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
from kanon.ingest import CHUNKING_VERSION, Chunk, chunk_document

MAX_ATTEMPTS = 3
BATCH = 64                  # chunks per embed call and per progress checkpoint
LEASE = "15 minutes"        # must exceed the slowest single parse; heartbeat renews it per batch
RETRY_BASE_SECONDS = 10     # backoff 10s, 20s: gives a rate-limited embedding API time to recover


class StaleClaimError(RuntimeError):
    """The lease/fence moved to another worker; this owner must not write or purge."""


TABLES = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
  idem_key       text PRIMARY KEY,
  doc_id         text NOT NULL,
  revision       text NOT NULL,
  authority_status text NOT NULL,
  source_sha256  text NOT NULL,
  expected_sha256 text,
  chunking_version int NOT NULL,
  document_generation bigint NOT NULL DEFAULT 0,
  claim_generation bigint NOT NULL DEFAULT 0,
  local_path     text NOT NULL,
  source_url     text NOT NULL DEFAULT '',
  page_range     int[],
  paginated      boolean NOT NULL,
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'parsing', 'validated', 'embedding',
                                   'published', 'failed', 'dead')),
  attempts       int NOT NULL DEFAULT 0,
  total_attempts int NOT NULL DEFAULT 0,
  done_batches   int NOT NULL DEFAULT 0,
  n_chunks       int,
  preflight_issue text,
  run_after      timestamptz NOT NULL DEFAULT now(),
  leased_until   timestamptz,
  worker         text,
  last_stage     text,
  errors         jsonb NOT NULL DEFAULT '[]',
  first_enqueued_at timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz,
  finished_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (doc_id, revision, authority_status, source_sha256, chunking_version)
);
CREATE TABLE IF NOT EXISTS document_fences (
  doc_key text PRIMARY KEY,
  generation bigint NOT NULL,
  current_idem_key text NOT NULL REFERENCES ingest_jobs(idem_key) ON DELETE RESTRICT,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
  id             text PRIMARY KEY,
  idem_key       text NOT NULL REFERENCES ingest_jobs ON DELETE RESTRICT,
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
  title          text NOT NULL DEFAULT '',
  family         text NOT NULL DEFAULT '',
  plants         text[] NOT NULL DEFAULT '{}',
  units          text[] NOT NULL DEFAULT '{}',
  dockets        text[] NOT NULL DEFAULT '{}',
  applicability_scope text NOT NULL DEFAULT 'unknown',
  applicability_source text NOT NULL DEFAULT 'unknown',
  n_chars        int NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc_key ON chunks (doc_key);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks (status, kind, effective_from, effective_to);
"""

EXTRAS = """
CREATE TABLE IF NOT EXISTS job_events (
  event_id       bigserial PRIMARY KEY,
  idem_key       text NOT NULL REFERENCES ingest_jobs ON DELETE RESTRICT,
  from_status    text,
  to_status      text NOT NULL,
  attempt        int NOT NULL,
  total_attempts int NOT NULL,
  worker         text,
  stage          text,
  error          jsonb,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  seconds_since_previous double precision
);
CREATE INDEX IF NOT EXISTS job_events_job_time ON job_events (idem_key, occurred_at, event_id);

CREATE OR REPLACE FUNCTION public.audit_ingest_job_status() RETURNS trigger AS $$
DECLARE previous_at timestamptz;
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
    SELECT max(occurred_at) INTO previous_at FROM public.job_events WHERE idem_key = NEW.idem_key;
    INSERT INTO public.job_events
      (idem_key, from_status, to_status, attempt, total_attempts, worker, stage, error,
       seconds_since_previous)
    VALUES
      (NEW.idem_key, CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END, NEW.status,
       NEW.attempts, NEW.total_attempts, NEW.worker, NEW.last_stage,
       CASE WHEN jsonb_array_length(NEW.errors) > 0 AND
                  (NEW.status IN ('failed', 'dead') OR
                   (TG_OP = 'UPDATE' AND
                    jsonb_array_length(NEW.errors) > jsonb_array_length(OLD.errors)))
            THEN NEW.errors -> -1 END,
       extract(epoch FROM (now() - coalesce(previous_at, NEW.first_enqueued_at))));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ingest_jobs_status_audit ON ingest_jobs;
CREATE TRIGGER ingest_jobs_status_audit
  AFTER INSERT OR UPDATE OF status ON ingest_jobs
  FOR EACH ROW EXECUTE FUNCTION public.audit_ingest_job_status();

CREATE OR REPLACE FUNCTION public.immutable_job_events() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'job_events is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS job_events_immutable ON job_events;
CREATE TRIGGER job_events_immutable BEFORE UPDATE OR DELETE ON job_events
  FOR EACH ROW EXECUTE FUNCTION public.immutable_job_events();

DROP VIEW IF EXISTS dead_letter_queue;
CREATE VIEW dead_letter_queue AS
  SELECT idem_key, doc_id, revision, authority_status, source_sha256, local_path, source_url,
         attempts, total_attempts, preflight_issue, last_stage, errors,
         first_enqueued_at, started_at, finished_at, updated_at
  FROM ingest_jobs WHERE status = 'dead';

CREATE OR REPLACE VIEW failing_documents AS
  SELECT doc_id, revision, max(source_url) AS source_url, count(*) AS jobs,
         sum(total_attempts) AS total_attempts,
         sum(jsonb_array_length(errors)) AS failures,
         max(updated_at) AS last_failure_at
  FROM ingest_jobs
  WHERE jsonb_array_length(errors) > 0
  GROUP BY doc_id, revision
  ORDER BY failures DESC, last_failure_at DESC;
"""

# A job whose worker stopped heartbeating (crash, OOM kill, hang). Appended to `errors` when the
# job is reclaimed or declared dead, so a crash counts as an attempt like any exception does.
IN_PROGRESS = ("parsing", "validated", "embedding")

_LEASE_LOST = """errors || jsonb_build_array(jsonb_build_object(
    'attempt', attempts, 'stage', last_stage, 'type', 'LeaseExpired', 'at', now(),
    'message', 'worker ' || coalesce(worker, '?') || ' stopped heartbeating (crash, kill or hang)',
    'diagnosis', 'worker died mid-job; if this repeats on the same file, the parser is crashing on it'))"""

def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    conn.execute(TABLES)
    _migrate_legacy_schema(conn)
    conn.execute(EXTRAS)
    return conn


def _migrate_legacy_schema(conn: psycopg.Connection) -> None:
    """Preserve databases created by the first queue prototype; new installs are no-ops."""
    conn.execute(f"""
      ALTER TABLE ingest_jobs DROP CONSTRAINT IF EXISTS ingest_jobs_status_check;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS authority_status text;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS source_sha256 text;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS expected_sha256 text;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS chunking_version int;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS document_generation bigint NOT NULL DEFAULT 0;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS claim_generation bigint NOT NULL DEFAULT 0;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS source_url text NOT NULL DEFAULT '';
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS total_attempts int NOT NULL DEFAULT 0;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS preflight_issue text;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS first_enqueued_at timestamptz;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz;
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT '';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS family text NOT NULL DEFAULT '';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS plants text[] NOT NULL DEFAULT '{{}}';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS units text[] NOT NULL DEFAULT '{{}}';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS dockets text[] NOT NULL DEFAULT '{{}}';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS applicability_scope text NOT NULL DEFAULT 'unknown';
      ALTER TABLE chunks ADD COLUMN IF NOT EXISTS applicability_source text NOT NULL DEFAULT 'unknown';
      UPDATE ingest_jobs SET status = CASE status WHEN 'running' THEN 'parsing'
                                                   WHEN 'done' THEN 'published' ELSE status END;
      UPDATE ingest_jobs SET authority_status = coalesce(authority_status,
        (SELECT c.status FROM chunks c WHERE c.idem_key = ingest_jobs.idem_key LIMIT 1), 'unknown'),
        source_sha256 = coalesce(source_sha256, 'legacy:' || idem_key),
        chunking_version = coalesce(chunking_version, {CHUNKING_VERSION}),
        total_attempts = greatest(total_attempts, attempts),
        first_enqueued_at = coalesce(first_enqueued_at, created_at),
        started_at = coalesce(started_at, CASE WHEN attempts > 0 THEN updated_at END),
        finished_at = coalesce(finished_at,
          CASE WHEN status IN ('published', 'dead') THEN updated_at END);
      ALTER TABLE ingest_jobs ALTER COLUMN authority_status SET NOT NULL;
      ALTER TABLE ingest_jobs ALTER COLUMN source_sha256 SET NOT NULL;
      ALTER TABLE ingest_jobs ALTER COLUMN chunking_version SET NOT NULL;
      ALTER TABLE ingest_jobs ALTER COLUMN first_enqueued_at SET NOT NULL;
      ALTER TABLE ingest_jobs ALTER COLUMN first_enqueued_at SET DEFAULT now();
      CREATE UNIQUE INDEX IF NOT EXISTS ingest_jobs_idempotency
        ON ingest_jobs (doc_id, revision, authority_status, source_sha256, chunking_version);
      DO $$ BEGIN
        ALTER TABLE ingest_jobs ADD CONSTRAINT ingest_jobs_status_check
          CHECK (status IN ('queued', 'parsing', 'validated', 'embedding',
                            'published', 'failed', 'dead'));
      EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      INSERT INTO document_fences (doc_key,generation,current_idem_key)
        SELECT DISTINCT ON (doc_id,revision) doc_id||'@'||revision,1,idem_key
        FROM ingest_jobs ORDER BY doc_id,revision,updated_at DESC
        ON CONFLICT (doc_key) DO NOTHING;
      UPDATE ingest_jobs j SET document_generation=f.generation
        FROM document_fences f WHERE f.current_idem_key=j.idem_key
          AND j.document_generation=0;
    """)


def _identity(doc_id: str, revision: str, authority_status: str, path: Path) -> tuple[str, str]:
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        value = digest.hexdigest()
    else:
        value = "missing"
    key = f"{doc_id}@{revision}:{authority_status}:{value}:c{CHUNKING_VERSION}"
    return key, value


def _manifest_path(data_dir: Path, local_path: str) -> Path:
    """Confine manifest paths, including symlinks, to the configured data directory."""
    base = data_dir.resolve()
    path = (base / local_path).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"local_path escapes data directory: {local_path!r}")
    return path


def idem_key(doc_id: str, revision: str, path: Path, authority_status: str = "unknown") -> str:
    """Readable key; the separate database columns are the authoritative idempotency key."""
    return _identity(doc_id, revision, authority_status, path)[0]


def enqueue_manifest(conn: psycopg.Connection, data_dir: Path, store) -> int:
    """Returns how many new jobs were queued; unchanged files already have a job."""
    added = 0
    for line in (data_dir / "manifest.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        path = _manifest_path(data_dir, row["local_path"])
        doc = store.get(row["doc_id"], row["revision"])
        authority_status = doc.status if doc else "unknown"
        source_url = (doc.source_url if doc else None) or row.get("source_url") or ""
        key, digest = _identity(row["doc_id"], row["revision"], authority_status, path)
        issue = preflight(path)
        declared = row.get("sha256")
        if declared and digest != "missing" and declared != digest:
            mismatch = f"sha256 mismatch: manifest={declared}, file={digest}"
            issue = f"{issue}; {mismatch}" if issue else mismatch
        with conn.transaction():
            doc_key = f"{row['doc_id']}@{row['revision']}"
            # Serialize identity changes with a worker publishing this same revision.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (doc_key,))
            inserted = conn.execute(
                "INSERT INTO ingest_jobs (idem_key,doc_id,revision,authority_status,source_sha256,"
                " expected_sha256,chunking_version,local_path,source_url,page_range,paginated,"
                " preflight_issue) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (doc_id,revision,authority_status,source_sha256,chunking_version)"
                " DO NOTHING RETURNING idem_key",
                (key,row["doc_id"],row["revision"],authority_status,digest,declared,
                 CHUNKING_VERSION,row["local_path"],source_url,row.get("page_range"),
                 row.get("paginated",True),issue)).fetchone()
            if not inserted:
                continue
            fence = conn.execute(
                "INSERT INTO document_fences (doc_key,generation,current_idem_key) VALUES (%s,1,%s)"
                " ON CONFLICT (doc_key) DO UPDATE SET generation=document_fences.generation+1,"
                " current_idem_key=EXCLUDED.current_idem_key,updated_at=now() RETURNING generation",
                (doc_key,key)).fetchone()
            conn.execute("UPDATE ingest_jobs SET document_generation=%s WHERE idem_key=%s",
                         (fence["generation"],key))
            error = {"type":"SupersededJob","stage":"enqueue",
                     "message":f"superseded by ingest identity {key}",
                     "diagnosis":"a newer file/status/chunking identity owns this revision",
                     "at":datetime.now(timezone.utc).isoformat()}
            conn.execute("UPDATE ingest_jobs SET status='dead',last_stage='superseded',"
                "errors=errors||%s,leased_until=NULL,finished_at=now(),updated_at=now()"
                " WHERE doc_id=%s AND revision=%s AND idem_key<>%s"
                " AND status IN ('queued','failed','parsing','validated','embedding')",
                (Jsonb([error]),row["doc_id"],row["revision"],key))
            added += 1
    return added


def claim(conn: psycopg.Connection, worker: str, index=None) -> dict | None:
    while True:
        dead = None
        with conn.transaction():
            job = conn.execute(
                "SELECT j.* FROM ingest_jobs j JOIN document_fences f"
                " ON f.current_idem_key=j.idem_key AND f.generation=j.document_generation"
                " WHERE ((j.status IN ('queued','failed') AND j.run_after<=now()) OR"
                " (j.status IN ('parsing','validated','embedding') AND j.leased_until<now()))"
                " ORDER BY j.created_at,j.idem_key FOR UPDATE OF j SKIP LOCKED LIMIT 1").fetchone()
            if not job:
                return None
            doc_key = f"{job['doc_id']}@{job['revision']}"
            locked = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok",
                                  (doc_key,)).fetchone()["ok"]
            if not locked:
                return None
            reclaimed = job["status"] in IN_PROGRESS
            if reclaimed and job["attempts"] >= MAX_ATTEMPTS:
                dead = conn.execute(
                    f"UPDATE ingest_jobs SET errors={_LEASE_LOST},status='dead',"
                    "last_stage=coalesce(last_stage,'worker'),leased_until=NULL,"
                    "finished_at=now(),updated_at=now() WHERE idem_key=%s RETURNING *",
                    (job["idem_key"],)).fetchone()
            else:
                return conn.execute(
                    f"UPDATE ingest_jobs SET errors=CASE WHEN %s THEN {_LEASE_LOST} ELSE errors END,"
                    "status='parsing',attempts=attempts+1,total_attempts=total_attempts+1,"
                    "claim_generation=claim_generation+1,worker=%s,last_stage='parse',"
                    "started_at=coalesce(started_at,now()),finished_at=NULL,"
                    "leased_until=now()+%s::interval,updated_at=now()"
                    " WHERE idem_key=%s RETURNING *",
                    (reclaimed,worker,LEASE,job["idem_key"])).fetchone()
        if dead:
            if index is not None:
                index.remove_ingest(dead["idem_key"])
            conn.execute("DELETE FROM chunks WHERE idem_key=%s", (dead["idem_key"],))
            _unlock(conn, doc_key)
            print(f"  DEAD  {dead['doc_id']} Rev {dead['revision']}: worker lost on final attempt")


def _owner(job: dict) -> tuple[str, tuple]:
    where = ("idem_key=%s AND claim_generation=%s AND worker=%s AND leased_until>=now() AND "
             "EXISTS (SELECT 1 FROM document_fences f"
             " WHERE f.current_idem_key=ingest_jobs.idem_key"
             " AND f.generation=ingest_jobs.document_generation)")
    return where, (job["idem_key"],job["claim_generation"],job["worker"])


def _heartbeat(conn, job: dict) -> None:
    where, params = _owner(job)
    result = conn.execute(f"UPDATE ingest_jobs SET leased_until=now()+%s::interval,"
                          f"updated_at=now() WHERE {where}", (LEASE,*params))
    if result.rowcount != 1:
        raise StaleClaimError(f"stale claim for {job['idem_key']}")


def _check_source(job: dict, path: Path) -> None:
    """Reject bytes changed since enqueue, including a missing file appearing later."""
    _, digest = _identity(job["doc_id"], job["revision"], job["authority_status"], path)
    if digest != job["source_sha256"]:
        raise PipelineIntegrityError("source sha256 changed since enqueue; enqueue the new content")
    if job["expected_sha256"] and digest != job["expected_sha256"]:
        raise PipelineIntegrityError(job["preflight_issue"] or "manifest sha256 mismatch")


def _unlock(conn, doc_key: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (doc_key,))


def process(conn: psycopg.Connection, job: dict, data_dir: Path, store, index, converter, chunker) -> None:
    """Run one fenced claim to `published`, or record a retry/dead-letter failure."""
    path, stage = _manifest_path(data_dir, job["local_path"]), "parse"
    doc_key = f"{job['doc_id']}@{job['revision']}"
    try:
        doc = store.get(job["doc_id"], job["revision"])
        if doc is None:
            raise LookupError(f"{doc_key} not in authority store")
        if doc.status != job["authority_status"]:
            raise PipelineIntegrityError("authority status changed since enqueue; enqueue new identity")
        _check_source(job, path)
        pr = tuple(job["page_range"]) if job["page_range"] else None
        # ponytail: resume assumes chunking is deterministic for a given key. Holds while Docling
        # is pinned by uv.lock; bump CHUNKING_VERSION on a Docling upgrade.
        chunks = chunk_document(path, doc, job["paginated"], converter, chunker, page_range=pr)
        stage = "validate"
        _check_source(job, path)
        check_chunks(chunks)                       # fail before paying for embeddings
        if any(c.status != job["authority_status"] for c in chunks):
            raise PipelineIntegrityError("chunk authority status differs from queued identity")
        _transition(conn, job, "validated", stage)
        if job["done_batches"] == 0:               # fresh start: drop any older file's chunks
            stage = "purge"
            _heartbeat(conn, job)
            _purge(conn, index, doc_key)
        _transition(conn, job, "embedding", "embed")
        for b in range(job["done_batches"], math.ceil(len(chunks) / BATCH)):
            part = chunks[b * BATCH:(b + 1) * BATCH]
            stage = "embed"
            _heartbeat(conn, job)
            vectors = index.embed(part)
            stage = "validate_vectors"
            index.validate_publish(part, vectors)  # Tier A gate: no vector write before this
            stage = "publish"
            _heartbeat(conn, job)
            _check_source(job, path)
            index.write(part, vectors, ingest_key=job["idem_key"])
            stage = "record"
            _heartbeat(conn, job)
            with conn.transaction():               # metadata and progress commit together
                _record(conn, job["idem_key"], part)
                where, params = _owner(job)
                result = conn.execute(f"UPDATE ingest_jobs SET done_batches=%s,last_stage='record',"
                    f"leased_until=now()+%s::interval,updated_at=now() WHERE {where}",
                    (b+1,LEASE,*params))
                if result.rowcount != 1:
                    raise StaleClaimError("claim lost before checkpoint")
        current = store.get(job["doc_id"],job["revision"])
        if current is None or current.status != job["authority_status"]:
            raise PipelineIntegrityError("authority status changed before publish; enqueue new identity")
        _check_source(job, path)
        _transition(conn, job, "published", "publish", renew=False,
                    n_chunks=len(chunks), finished_at=datetime.now(timezone.utc))
        print(f"  done  {doc_key}: {len(chunks)} chunks")
    except StaleClaimError as exc:
        print(f"  stale {doc_key}: {exc}")
    except Exception as exc:
        _fail(conn, index, job, path, stage, exc)
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (doc_key,))


def run_worker(conn, data_dir: Path, store, index, converter, chunker, poll: float = 2.0) -> None:
    """Work until no job is queued, running elsewhere, or waiting out a retry backoff."""
    worker = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        job = claim(conn, worker, index)
        if job:
            process(conn, job, data_dir, store, index, converter, chunker)
        elif conn.execute("SELECT 1 FROM ingest_jobs WHERE status IN"
                          " ('queued', 'failed', 'parsing', 'validated', 'embedding')").fetchone():
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
        " leased_until = NULL, worker = NULL, finished_at = NULL, updated_at = now()"
        " WHERE idem_key = %s AND status = 'dead' AND EXISTS"
        " (SELECT 1 FROM document_fences f WHERE f.current_idem_key=ingest_jobs.idem_key"
        " AND f.generation=ingest_jobs.document_generation)", (key,)).rowcount)


def dead_letter_report(conn) -> str:
    rows = conn.execute("SELECT * FROM dead_letter_queue ORDER BY updated_at").fetchall()
    if not rows:
        return "dead letter queue is empty"
    out = [f"{len(rows)} document(s) in the dead letter queue\n"]
    for r in rows:
        errs = r["errors"]
        same = len({(e.get("type"), e.get("message")) for e in errs}) == 1
        out += [f"{r['doc_id']} Rev {r['revision']}   {r['local_path']}",
                f"  key:       {r['idem_key']}",
                f"  source:    {r['source_url'] or '(not recorded)'}",
                f"  attempts:  {r['attempts']} this run / {r['total_attempts']} lifetime",
                f"  diagnosis: {errs[-1].get('diagnosis', 'no diagnosis recorded') if errs else 'no diagnosis recorded'}",
                f"  pattern:   {'same error every attempt: the file, not the system' if same else 'errors differ across attempts: likely transient/infra'}"]
        if r["preflight_issue"]:
            out.append(f"  preflight: {r['preflight_issue']}")
        for e in errs:
            out.append(f"  attempt {e.get('attempt', '-')} [{e.get('stage', '-')}] "
                       f"{e.get('type', 'Error')}: {e.get('message', '')[:200]}")
        out.append(f"  requeue after fixing: kanon dlq --requeue '{r['idem_key']}'\n")
    return "\n".join(out)


def failing_documents_report(conn) -> str:
    rows = conn.execute("SELECT * FROM failing_documents").fetchall()
    if not rows:
        return "no ingest failures recorded"
    out = [f"{len(rows)} document revision(s) with recorded ingest failures"]
    for r in rows:
        out.append(f"{r['doc_id']} Rev {r['revision']}: {r['failures']} failure(s), "
                   f"{r['total_attempts']} total attempt(s), {r['jobs']} content/status job(s)"
                   f"\n  source: {r['source_url'] or '(not recorded)'}"
                   f"\n  last:   {r['last_failure_at']}")
    return "\n".join(out)


def preflight(path: Path) -> str | None:
    """Cheap enqueue-time diagnostics. Bad files still get the normal three worker attempts."""
    if not path.exists():
        return "file missing: re-fetch it or fix local_path in data/manifest.jsonl"
    if path.stat().st_size == 0:
        return "empty file: the download was truncated to 0 bytes"
    if path.suffix.lower() == ".pdf":
        size = path.stat().st_size
        with path.open("rb") as source:
            head = source.read(1024)
            source.seek(max(0, size - 2048))
            tail = source.read()
        issues = []
        if b"%PDF-" not in head:
            issues.append(f"not a PDF (no %PDF- header; starts {head[:40]!r})")
        if b"%%EOF" not in tail:
            issues.append("truncated PDF (no %%EOF trailer)")
        with path.open("rb") as source:
            encrypted = any(b"/Encrypt" in block
                            for block in iter(lambda: source.read(1024 * 1024), b""))
        if encrypted:
            issues.append("encrypted PDF")
        return "; ".join(issues) or None
    return None


def diagnose(path: Path, exc: BaseException) -> str:
    """Name the likely real cause, so a reviewer starts from the file, not a stack trace."""
    if not path.exists():
        return "file missing: re-fetch it or fix local_path in data/manifest.jsonl"
    if path.stat().st_size == 0:
        return "empty file: the download was truncated to 0 bytes"
    if isinstance(exc, LookupError):
        return "document not in the authority store: run `kanon load` before ingest"
    if issue := preflight(path):
        return issue
    if isinstance(exc, PipelineIntegrityError) and "zero chunks" in str(exc):
        return "no extractable text: probably a scanned PDF with no text layer (OCR is off by design)"
    if type(exc).__name__ in ("RateLimitError", "APIConnectionError", "APITimeoutError",
                              "InternalServerError", "OperationalError"):
        return "transient service error (embedding API / database): requeue once it recovers"
    return "unclassified: read the traceback stored on the row"


def _transition(conn, job: dict, status: str, stage: str, renew: bool = True, **values) -> None:
    allowed = {"n_chunks", "finished_at"}
    if not set(values) <= allowed:
        raise ValueError("unsupported transition fields")
    where, owner = _owner(job)
    sets, params = ["status=%s", "last_stage=%s", "updated_at=now()"], [status,stage]
    if renew:
        sets.append("leased_until=now()+%s::interval")
        params.append(LEASE)
    else:
        sets.append("leased_until=NULL")
    for field,value in values.items():
        sets.append(f"{field}=%s")
        params.append(value)
    result = conn.execute(f"UPDATE ingest_jobs SET {','.join(sets)} WHERE {where}",
                          (*params,*owner))
    if result.rowcount != 1:
        raise StaleClaimError(f"stale claim for {job['idem_key']}")


def _record(conn, key: str, part: list[Chunk]) -> None:
    conn.cursor().executemany(
        "INSERT INTO chunks (id, idem_key, doc_id, revision, doc_key, kind, status, effective_from,"
        " effective_to, issue_date, section, page, paginated, accession, source_url, title, family,"
        " plants, units, dockets, applicability_scope, applicability_source, n_chars)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (id) DO UPDATE SET idem_key=EXCLUDED.idem_key,status=EXCLUDED.status,"
        " section=EXCLUDED.section,page=EXCLUDED.page,title=EXCLUDED.title,family=EXCLUDED.family,"
        " plants=EXCLUDED.plants,units=EXCLUDED.units,dockets=EXCLUDED.dockets,"
        " applicability_scope=EXCLUDED.applicability_scope,"
        " applicability_source=EXCLUDED.applicability_source,n_chars=EXCLUDED.n_chars",
        [(c.id, key, c.doc_id, c.revision, c.doc_key, c.kind, c.status, c.effective_from or None,
          c.effective_to or None, c.issue_date or None, c.section, c.page, c.paginated,
          c.accession, c.source_url, c.title, c.family, c.plants, c.units, c.dockets,
          c.applicability_scope, c.applicability_source, len(c.text)) for c in part])


def _purge(conn, index, doc_key: str) -> None:
    index.remove_doc(doc_key)
    conn.execute("DELETE FROM chunks WHERE doc_key = %s", (doc_key,))


def _fail(conn, index, job: dict, path: Path, stage: str, exc: Exception) -> None:
    dead = job["attempts"] >= MAX_ATTEMPTS
    entry = {"attempt": job["attempts"], "stage": stage, "type": type(exc).__name__,
             "message": str(exc), "diagnosis": diagnose(path, exc),
             "traceback": traceback.format_exc(limit=8),
             "at": datetime.now(timezone.utc).isoformat()}
    where, owner = _owner(job)
    result = conn.execute(
        "UPDATE ingest_jobs SET status = %s, errors = errors || %s, last_stage = %s,"
        " done_batches = CASE WHEN %s THEN 0 ELSE done_batches END,"
        " run_after = now() + %s * interval '1 second', leased_until = NULL,"
        " finished_at = CASE WHEN %s THEN now() ELSE NULL END, updated_at = now()"
        f" WHERE {where}",
        ("dead" if dead else "failed", Jsonb([entry]), stage, dead,
         RETRY_BASE_SECONDS * 2 ** (job["attempts"] - 1), dead, *owner))
    if result.rowcount != 1:
        print(f"  stale {job['doc_id']} Rev {job['revision']}: old owner did not record failure")
        return
    if dead:
        index.remove_ingest(job["idem_key"])
        conn.execute("DELETE FROM chunks WHERE idem_key=%s", (job["idem_key"],))
    print(f"  {'DEAD ' if dead else 'retry'} {job['doc_id']} Rev {job['revision']}"
          f" attempt {job['attempts']}/{MAX_ATTEMPTS} [{stage}] {type(exc).__name__}: {str(exc)[:120]}")
