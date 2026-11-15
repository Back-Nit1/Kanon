# Ingest verification process

This process is the acceptance contract for a document moving from the manifest to the
searchable index. A job is not reported as `published` until every step below succeeds.

## State and evidence

The lifecycle is `queued -> parsing -> validated -> embedding -> published`. A recoverable
error moves the job to `failed`; a third failed attempt moves it to `dead`. Every insert and
state change is copied by a Postgres trigger to the append-only `job_events` table in the same
database transaction. `kanon dlq --failing` uses that history to identify repeat failures.

Postgres and Chroma do not share a transaction. A vector upsert can succeed immediately before
the worker dies and before its Postgres checkpoint commits. Recovery therefore treats vector
writes as at-least-once: chunk IDs are deterministic and Chroma writes are upserts, so the next
worker safely repeats the uncheckpointed batch. The audit log proves Postgres state changes; it
does not claim atomicity with Chroma. Vectors are searchable batch by batch before the job is
published, and an in-flight Chroma request may complete after its Postgres connection is lost.
The database lease and advisory lock cannot fence that request inside Chroma; reconciliation
or a transactional publication mechanism is needed for strict atomic visibility and fencing.
The session advisory lock serializes live workers and enqueues for each revision; a hung session
can delay work on that revision until the connection closes.

## Verification gates

1. **Enqueue identity and preflight**
   - Read authority status and source URL from `AuthorityStore`, never from the manifest.
   - Compute the full file SHA-256 by streaming the bytes.
   - Recheck the source digest during processing, and reject a manifest SHA-256 mismatch.
   - Enforce one job for `(doc_id, revision, authority_status, content_sha256,
     chunking_version)`. Re-adding the same identity is a no-op, including after it is dead.
   - Record obvious file faults (`missing`, empty, invalid/truncated/encrypted PDF) in
     `preflight_issue`. The job still receives the normal three attempts so preflight cannot
     become an unreviewed rejection path.

2. **Parse validation**
   - Produce at least one chunk.
   - Require valid page provenance and all authority metadata used by retrieval.
   - Require each parsed chunk's authority status to match the status captured in the job.

3. **Pre-publish embedding validation**
   - Require one vector per chunk, non-empty numeric vectors, and one consistent dimension.
   - When the configured embedding dimension is known, require an exact match.
   - Validate every Chroma metadata object and routing decision: only `active` chunks may enter
     hot; every other authority status must enter cold.
   - Only after those checks pass may the batch be upserted.

4. **Post-write verification**
   - Save chunk metadata and the completed-batch checkpoint in one Postgres transaction.
   - Mark the job `published` only after all vector writes and checkpoints finish.
   - At the end of `kanon ingest`, compare Postgres chunk IDs to Chroma, verify dimensions and
     metadata, assert hot-tier purity, and perform a round-trip retrieval.

## Required tests

- unchanged files are no-ops, including dead jobs; changed bytes, authority status, or chunking
  version create a new job;
- two enqueuers racing on the same manifest create one job, and two workers never claim the
  same job;
- a worker crash is reclaimed from every in-progress state and deterministic upserts do not
  duplicate chunks;
- malformed files receive three attempts and then retain actionable DLQ evidence;
- invalid metadata, routing, vector count, or vector dimension fails before the vector upsert;
- the audit trigger records every transition and refuses update/delete of its event rows;
- a requeue resets the three-attempt cycle but never resets `total_attempts`.

