"""Ingest queue against a real Postgres (docker compose up -d). Skipped if none is reachable.

Embeddings/vector store are faked: these tests check claim, resume, retry and dead-letter
semantics, which live entirely in Postgres. The corrupt-PDF test uses the real Docling parser.
"""

import json
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
        c.execute("DROP VIEW IF EXISTS dead_letter_queue; DROP TABLE IF EXISTS chunks, ingest_jobs")
    c = queue.connect(TEST_DSN)
    yield c
    c.close()


@pytest.fixture
def store():
    s = AuthorityStore(":memory:")
    s.upsert_document(DOC)
    return s


class FakeIndex:
    def __init__(self, crash_on_call: int | None = None):
        self.added, self.removed, self.calls, self.crash_on_call = [], [], 0, crash_on_call

    def add(self, chunks):
        self.calls += 1
        if self.calls == self.crash_on_call:
            raise SystemExit("simulated worker crash")   # BaseException: not caught, like a kill
        self.added += [c.id for c in chunks]

    def remove_doc(self, doc_key):
        self.removed.append(doc_key)


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


def test_enqueue_is_idempotent_per_file_and_chunker(conn, tmp_path, monkeypatch):
    d = _data_dir(tmp_path, b"%PDF-1.4 v1")
    assert queue.enqueue_manifest(conn, d) == 1
    assert queue.enqueue_manifest(conn, d) == 0          # same bytes, same key: no duplicate job
    (d / "doc.pdf").write_bytes(b"%PDF-1.4 v2")
    assert queue.enqueue_manifest(conn, d) == 1          # changed file: new key, new job
    monkeypatch.setattr(queue, "CHUNKING_VERSION", 99)
    assert queue.enqueue_manifest(conn, d) == 1          # changed chunker: everything re-queues


def test_concurrent_workers_never_claim_the_same_job(conn, tmp_path):
    for i in range(2):
        conn.execute("INSERT INTO ingest_jobs (idem_key, doc_id, revision, local_path, paginated)"
                     " VALUES (%s, 'd', %s, 'x.pdf', true)", (f"k{i}", str(i)))
    other = queue.connect(TEST_DSN)
    a, b = queue.claim(conn, "w1"), queue.claim(other, "w2")
    assert {a["idem_key"], b["idem_key"]} == {"k0", "k1"}
    assert queue.claim(conn, "w1") is None
    other.close()


def test_corrupt_pdf_retries_three_times_then_dead_letters(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_SECONDS", 0)
    d = _data_dir(tmp_path, b"<html><body>503 Service Unavailable</body></html>")
    queue.enqueue_manifest(conn, d)
    index = FakeIndex()
    queue.run_worker(conn, d, store, index, make_converter(), HybridChunker(), poll=0.05)

    job = conn.execute("SELECT * FROM ingest_jobs").fetchone()
    assert job["status"] == "dead" and job["attempts"] == 3 and len(job["errors"]) == 3
    assert all(e["stage"] == "parse" for e in job["errors"])
    assert "not a PDF" in job["errors"][-1]["diagnosis"]
    assert index.added == [] and index.removed == [f"{DOC.doc_id}@{DOC.revision}"]
    report = queue.dead_letter_report(conn)
    assert "not a PDF" in report and "same error every attempt" in report
    assert queue.requeue(conn, job["idem_key"])
    assert conn.execute("SELECT status, attempts FROM ingest_jobs").fetchone() == \
        {"status": "queued", "attempts": 0}


def test_crashed_job_resumes_from_last_batch_without_duplicates(conn, store, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "BATCH", 2)
    monkeypatch.setattr(queue, "chunk_document", lambda *a, **k: _chunks(5))   # 3 batches
    d = _data_dir(tmp_path, b"%PDF-1.4 fake")
    queue.enqueue_manifest(conn, d)

    first = FakeIndex(crash_on_call=2)                    # dies embedding batch 1
    with pytest.raises(SystemExit):
        queue.run_worker(conn, d, store, first, None, None)
    assert first.added == [c.id for c in _chunks(2)]
    conn.execute("UPDATE ingest_jobs SET leased_until = now() - interval '1 second'")  # lease runs out

    second = FakeIndex()
    queue.run_worker(conn, d, store, second, None, None)
    job = conn.execute("SELECT * FROM ingest_jobs").fetchone()
    assert job["status"] == "done" and job["attempts"] == 2 and job["n_chunks"] == 5
    assert job["errors"][0]["type"] == "LeaseExpired"
    assert second.added == [c.id for c in _chunks(5)[2:]]   # batch 0 not re-embedded
    assert second.removed == []                              # resume never purges
    n = conn.execute("SELECT count(*) AS n, count(DISTINCT id) AS d FROM chunks").fetchone()
    assert n == {"n": 5, "d": 5}
    assert conn.execute("SELECT section FROM chunks LIMIT 1").fetchone()["section"].startswith("50.59")


def test_crash_on_final_attempt_goes_to_dead_letter(conn):
    conn.execute("INSERT INTO ingest_jobs (idem_key, doc_id, revision, local_path, paginated, status,"
                 " attempts, leased_until) VALUES ('k', 'd', 'r', 'x.pdf', true, 'running', 3,"
                 " now() - interval '1 second')")
    assert queue.claim(conn, "w") is None
    job = conn.execute("SELECT status, errors FROM ingest_jobs").fetchone()
    assert job["status"] == "dead" and job["errors"][-1]["type"] == "LeaseExpired"
