"""Ingest queue against a real Postgres (docker compose up -d). Skipped if none is reachable.

Embeddings/vector store are faked: these tests check claim, resume, retry and dead-letter
semantics, which live entirely in Postgres. The corrupt-PDF test uses the real Docling parser.
"""

import json
import threading
from datetime import date

import psycopg
import pytest
from docling.chunking import HybridChunker

from kanon import queue
from kanon.ingest import Chunk, make_converter
from kanon.store import AuthorityStore, DocumentVersion

ADMIN_DSN = "postgresql://kanon:kanon@localhost:5432/kanon"
TEST_DSN = "postgresql://kanon:kanon@localhost:5432/kanon_test"
DOC = DocumentVersion(doc_id="10CFR50.59", revision="2002-01-01", family="10CFR50.59",
                      kind="regulation", status="active", effective_from=date(2001, 3, 13),
                      issue_date=date(2002, 1, 1))


@pytest.fixture
def conn():
    try:
        admin = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("no Postgres on localhost:5432 (docker compose up -d)")
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'kanon_test'").fetchone():
        admin.execute("CREATE DATABASE kanon_test")
    admin.close()
    with psycopg.connect(TEST_DSN, autocommit=True) as c:
        c.execute("DROP VIEW IF EXISTS dead_letter_queue, failing_documents;"
                  " DROP TABLE IF EXISTS job_events, chunks, document_fences, ingest_jobs")
    c = queue.connect(TEST_DSN)
    yield c
    c.close()


@pytest.fixture
def store():
    s = AuthorityStore(":memory:")
    s.upsert_document(DOC)
    return s


class FakeIndex:
    expected_dim = 3

    def __init__(self, crash_on_call: int | None = None, bad_dim: bool = False):
        self.added, self.removed, self.removed_ingests, self.calls = [], [], [], 0
        self.crash_on_call, self.bad_dim = crash_on_call, bad_dim

    def embed(self, chunks):
        self.calls += 1
        if self.calls == self.crash_on_call:
            raise SystemExit("simulated worker crash")   # BaseException: not caught, like a kill
        return [[0.1] * (2 if self.bad_dim else self.expected_dim) for _ in chunks]

    def validate_publish(self, chunks, vectors):
        if any(len(v) != self.expected_dim for v in vectors):
            raise ValueError("embedding dim mismatch")

    def write(self, chunks, vectors, ingest_key=None):
        self.validate_publish(chunks, vectors)
        self.added += [c.id for c in chunks]

    def remove_doc(self, doc_key):
        self.removed.append(doc_key)

    def remove_ingest(self, ingest_key):
        self.removed_ingests.append(ingest_key)


def _data_dir(tmp_path, content: bytes, name="doc.pdf"):
    (tmp_path / name).write_bytes(content)
    (tmp_path / "manifest.jsonl").write_text(json.dumps(
        {"doc_id": DOC.doc_id, "revision": DOC.revision, "local_path": name}) + "\n")
    return tmp_path


def _chunks(n):
    return [Chunk(id=f"{DOC.doc_id}@{DOC.revision}#{i:04d}", text=f"Section 50.59 > (c) text {i}",
                  doc_id=DOC.doc_id, revision=DOC.revision, kind=DOC.kind, status=DOC.status,
                  effective_from="2001-03-13", effective_to=None, issue_date="2002-01-01",
                  section="50.59 Changes, tests, and experiments", page=1 + i // 2,
                  accession="", source_url="", paginated=True) for i in range(n)]


def test_enqueue_is_idempotent_per_status_file_and_chunker(conn, store, tmp_path, monkeypatch):
    d = _data_dir(tmp_path, b"%PDF-1.4 v1")
    assert queue.enqueue_manifest(conn, d, store) == 1
    assert queue.enqueue_manifest(conn, d, store) == 0   # same tuple: no duplicate job
    job = conn.execute("SELECT * FROM ingest_jobs").fetchone()
    assert job["authority_status"] == "active"
    assert len(job["source_sha256"]) == 64
    assert "truncated PDF" in job["preflight_issue"]     # diagnostic; job remains queued
    (d / "doc.pdf").write_bytes(b"%PDF-1.4 v2")
    assert queue.enqueue_manifest(conn, d, store) == 1   # changed file: new key, new job
    monkeypatch.setattr(queue, "CHUNKING_VERSION", 99)
    assert queue.enqueue_manifest(conn, d, store) == 1   # changed chunker: everything re-queues
    store.upsert_document(DOC.model_copy(update={"status": "superseded"}))
    assert queue.enqueue_manifest(conn, d, store) == 1   # authority change re-tiers the revision


def test_concurrent_workers_never_claim_the_same_job(conn, tmp_path):
    for i in range(2):
        conn.execute("INSERT INTO ingest_jobs (idem_key, doc_id, revision, authority_status,"
                     " source_sha256, chunking_version, local_path, paginated)"
                     " VALUES (%s, 'd', %s, 'active', %s, 2, 'x.pdf', true)",
                     (f"k{i}", str(i), f"sha{i}"))
        conn.execute("INSERT INTO document_fences (doc_key, generation, current_idem_key)"
                     " VALUES (%s, 1, %s)", (f"d@{i}", f"k{i}"))
        conn.execute("UPDATE ingest_jobs SET document_generation=1 WHERE idem_key=%s", (f"k{i}",))
    other = queue.connect(TEST_DSN)
    a, b = queue.claim(conn, "w1"), queue.claim(other, "w2")
    assert {a["idem_key"], b["idem_key"]} == {"k0", "k1"}
    assert queue.claim(conn, "w1") is None
    other.close()


def test_corrupt_pdf_retries_three_times_then_dead_letters(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_SECONDS", 0)
    d = _data_dir(tmp_path, b"<html><body>503 Service Unavailable</body></html>")
    queue.enqueue_manifest(conn, d, store)
    index = FakeIndex()
    queue.run_worker(conn, d, store, index, make_converter(), HybridChunker(), poll=0.05)

    job = conn.execute("SELECT * FROM ingest_jobs").fetchone()
    assert job["status"] == "dead" and job["attempts"] == 3 and len(job["errors"]) == 3
    assert all(e["stage"] == "parse" for e in job["errors"])
    assert "not a PDF" in job["errors"][-1]["diagnosis"]
    assert index.added == [] and len(index.removed_ingests) == 1
    report = queue.dead_letter_report(conn)
    assert "not a PDF" in report and "same error every attempt" in report
    assert queue.requeue(conn, job["idem_key"])
    assert conn.execute("SELECT status, attempts, total_attempts FROM ingest_jobs").fetchone() == \
        {"status": "queued", "attempts": 0, "total_attempts": 3}


def test_crashed_job_resumes_from_last_batch_without_duplicates(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "BATCH", 2)
    monkeypatch.setattr(queue, "chunk_document", lambda *a, **k: _chunks(5))   # 3 batches
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    queue.enqueue_manifest(conn, d, store)

    first = FakeIndex(crash_on_call=2)                    # dies embedding batch 1
    with pytest.raises(SystemExit):
        queue.run_worker(conn, d, store, first, None, None)
    assert first.added == [c.id for c in _chunks(2)]
    conn.execute("UPDATE ingest_jobs SET leased_until = now() - interval '1 second'")  # lease runs out

    second = FakeIndex()
    queue.run_worker(conn, d, store, second, None, None)
    job = conn.execute("SELECT * FROM ingest_jobs").fetchone()
    assert job["status"] == "published" and job["attempts"] == 2 and job["n_chunks"] == 5
    assert job["errors"][0]["type"] == "LeaseExpired"
    assert second.added == [c.id for c in _chunks(5)[2:]]   # batch 0 not re-embedded
    assert second.removed == []                              # resume never purges
    n = conn.execute("SELECT count(*) AS n, count(DISTINCT id) AS d FROM chunks").fetchone()
    assert n == {"n": 5, "d": 5}
    assert conn.execute("SELECT section FROM chunks LIMIT 1").fetchone()["section"].startswith("50.59")


def test_crash_on_final_attempt_goes_to_dead_letter(conn):
    conn.execute("INSERT INTO ingest_jobs (idem_key, doc_id, revision, authority_status,"
                 " source_sha256, chunking_version, local_path, paginated, status, attempts,"
                 " total_attempts, leased_until) VALUES"
                 " ('k', 'd', 'r', 'active', 'sha', 2, 'x.pdf', true, 'embedding', 3, 3,"
                 " now() - interval '1 second')")
    conn.execute("INSERT INTO document_fences (doc_key,generation,current_idem_key)"
                 " VALUES ('d@r',1,'k')")
    conn.execute("UPDATE ingest_jobs SET document_generation=1 WHERE idem_key='k'")
    index = FakeIndex()
    assert queue.claim(conn, "w", index) is None
    job = conn.execute("SELECT status, errors FROM ingest_jobs").fetchone()
    assert job["status"] == "dead" and job["errors"][-1]["type"] == "LeaseExpired"
    assert index.removed == [] and index.removed_ingests == ["k"]


def test_lifecycle_is_trigger_audited_and_events_are_append_only(
        conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "chunk_document", lambda *a, **k: _chunks(2))
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    assert queue.enqueue_manifest(conn, d, store) == 1
    queue.run_worker(conn, d, store, FakeIndex(), None, None)

    events = conn.execute("SELECT from_status, to_status, seconds_since_previous"
                          " FROM job_events ORDER BY event_id").fetchall()
    assert [e["to_status"] for e in events] == \
        ["queued", "parsing", "validated", "embedding", "published"]
    assert all(e["seconds_since_previous"] >= 0 for e in events)
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("UPDATE job_events SET stage = 'tampered'")


def test_bad_vectors_never_publish_and_dead_letter_after_three_attempts(
        conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(queue, "chunk_document", lambda *a, **k: _chunks(2))
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    queue.enqueue_manifest(conn, d, store)
    index = FakeIndex(bad_dim=True)
    queue.run_worker(conn, d, store, index, None, None, poll=0.01)

    job = conn.execute("SELECT status, total_attempts, last_stage FROM ingest_jobs").fetchone()
    assert job == {"status": "dead", "total_attempts": 3, "last_stage": "validate_vectors"}
    assert index.added == []
    assert "3 failure(s)" in queue.failing_documents_report(conn)


def test_two_enqueuers_racing_on_same_manifest_create_one_job(conn, store, tmp_path):
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    other = queue.connect(TEST_DSN)
    barrier = threading.Barrier(2)
    results = []

    class Store:
        def get(self, doc_id, revision):
            return DOC

    def add(c):
        barrier.wait()
        results.append(queue.enqueue_manifest(c, d, Store()))

    threads = [threading.Thread(target=add, args=(c,)) for c in (conn, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    other.close()

    assert sorted(results) == [0, 1]
    assert conn.execute("SELECT count(*) AS n FROM ingest_jobs").fetchone()["n"] == 1


def test_manifest_digest_mismatch_consumes_attempts_without_publishing(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_SECONDS", 0)
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    manifest = json.loads((d / "manifest.jsonl").read_text())
    manifest["sha256"] = "0" * 64
    (d / "manifest.jsonl").write_text(json.dumps(manifest) + "\n")
    queue.enqueue_manifest(conn, d, store)
    index = FakeIndex()
    queue.run_worker(conn, d, store, index, None, None, poll=0.01)
    job = conn.execute("SELECT status, total_attempts, preflight_issue FROM ingest_jobs").fetchone()
    assert job["status"] == "dead" and job["total_attempts"] == 3
    assert "sha256 mismatch" in job["preflight_issue"] and index.added == []
    assert queue.enqueue_manifest(conn, d, store) == 0


def test_changed_source_cannot_publish_under_old_identity(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_SECONDS", 0)
    d = _data_dir(tmp_path, b"original")
    queue.enqueue_manifest(conn, d, store)
    (d / "doc.pdf").write_bytes(b"changed")
    index = FakeIndex()
    queue.run_worker(conn, d, store, index, None, None, poll=0.01)
    assert conn.execute("SELECT status FROM ingest_jobs").fetchone()["status"] == "dead"
    assert index.added == []


def test_expired_claim_cannot_update_or_fail_reclaimed_job(conn, store, tmp_path):
    d = _data_dir(tmp_path, b"file")
    queue.enqueue_manifest(conn, d, store)
    old = queue.claim(conn, "old")
    conn.execute("UPDATE ingest_jobs SET leased_until=now()-interval '1 second'")
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (f"{DOC.doc_id}@{DOC.revision}",))
    other = queue.connect(TEST_DSN)
    try:
        new = queue.claim(other, "new")
        assert new["claim_generation"] > old["claim_generation"]
        with pytest.raises(queue.StaleClaimError):
            queue._transition(conn, old, "validated", "validate")
        index = FakeIndex()
        queue._fail(conn, index, old, d / "doc.pdf", "parse", ValueError("old"))
        assert index.removed_ingests == []
        assert conn.execute("SELECT status, worker FROM ingest_jobs").fetchone() == {
            "status": "parsing", "worker": "new"}
    finally:
        other.close()


def test_chunk_sidecar_preserves_applicability_metadata(conn, store, tmp_path, monkeypatch):
    chunk = _chunks(1)[0].model_copy(update={"title": "Title", "family": "Family",
        "plants": ["Plant"], "units": ["1"], "dockets": ["50-123"],
        "applicability_scope": "plant", "applicability_source": "manifest"})
    monkeypatch.setattr(queue, "chunk_document", lambda *a, **k: [chunk])
    d = _data_dir(tmp_path, b"file")
    queue.enqueue_manifest(conn, d, store)
    queue.run_worker(conn, d, store, FakeIndex(), None, None)
    row = conn.execute("SELECT title, family, plants, units, dockets, applicability_scope,"
                       " applicability_source FROM chunks").fetchone()
    assert row == {name: getattr(chunk, name) for name in row}


def test_legacy_schema_migration_preserves_jobs(conn):
    conn.execute("DROP VIEW dead_letter_queue, failing_documents;"
                 " DROP TABLE job_events, chunks, document_fences, ingest_jobs")
    conn.execute("""
      CREATE TABLE ingest_jobs (
        idem_key text PRIMARY KEY, doc_id text NOT NULL, revision text NOT NULL,
        local_path text NOT NULL, page_range int[], paginated boolean NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead')),
        attempts int NOT NULL DEFAULT 0, done_batches int NOT NULL DEFAULT 0, n_chunks int,
        run_after timestamptz NOT NULL DEFAULT now(), leased_until timestamptz, worker text,
        last_stage text, errors jsonb NOT NULL DEFAULT '[]',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE chunks (
        id text PRIMARY KEY, idem_key text NOT NULL REFERENCES ingest_jobs ON DELETE CASCADE,
        doc_id text NOT NULL, revision text NOT NULL, doc_key text NOT NULL,
        kind text NOT NULL, status text NOT NULL, effective_from date, effective_to date,
        issue_date date, section text NOT NULL, page int NOT NULL, paginated boolean NOT NULL,
        accession text NOT NULL, source_url text NOT NULL, n_chars int NOT NULL);
      INSERT INTO ingest_jobs (idem_key,doc_id,revision,local_path,paginated,status,attempts)
        VALUES ('old','d','1','file',true,'done',2);
    """)
    migrated = queue.connect(TEST_DSN)
    try:
        row = migrated.execute("SELECT status, total_attempts, source_sha256 FROM ingest_jobs").fetchone()
        assert row == {"status": "published", "total_attempts": 2, "source_sha256": "legacy:old"}
        assert migrated.execute("SELECT current_idem_key FROM document_fences").fetchone()["current_idem_key"] == "old"
    finally:
        migrated.close()
