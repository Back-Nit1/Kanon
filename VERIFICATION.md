# Ingest verification contract

`kanon ingest` is the only path from the manifest into the searchable index. It runs one
document at a time in one process and **halts on the first failure** instead of quarantining it:
a document missing from the index is a document the system will silently fail to cite, so the
failure has to be visible now. There is no retry, no lease and no dead-letter queue — the
recovery action is to fix the input and run the command again.

Re-running is safe by construction: chunk IDs are deterministic (`doc_id@revision#NNNN`), a
revision's old chunks are deleted before its new ones are written, and Chroma writes are upserts.
A re-run rewrites the same rows.

## Per-document gates, in order

1. **Path confinement** — the manifest's `local_path` is resolved under `KANON_DATA_DIR`, with
   symlinks followed; anything escaping that directory is rejected before the parser sees it.
2. **Authority identity** — the `(doc_id, revision)` must already exist in `AuthorityStore`
   (`kanon load`). Status, dates and source URL are read from the store, never from the manifest.
3. **Source digest** — the file's streamed SHA-256 must equal the manifest's `sha256`. A
   mismatch means the bytes changed since the manifest was verified, and nothing is parsed.
4. **Parse validation** (Tier A, before any embedding is paid for) — at least one chunk; every
   chunk carries a valid 1-based page, and `doc_id`, `revision`, `status`, `kind` and (for
   requirements, not records) `effective_from`.
5. **Pre-write validation** — one finite vector per chunk, one consistent dimension matching the
   configured model, full Chroma metadata per chunk, and a legal authority status. Only then is
   the batch upserted, routed `active` → hot and every other status → cold.

## Whole-index gates at the end of the run

`kanon.checks.run_all`:

- embedding count equals the chunk count the run produced;
- every stored vector matches the configured dimension;
- every stored chunk carries the required metadata keys and a page;
- the hot collection holds **zero** non-active chunks — asserted over every row, not sampled;
- round-trip: a known chunk is retrievable in the top 5 for its own text.

Any failure raises `PipelineIntegrityError`. Nothing self-repairs.

## Tests that hold this up

`tests/test_pipeline.py`, on the two real GovInfo CFR editions in `data/raw/govinfo`:

- the manifest run routes the superseded 1997 text to cold and the active text to hot, and
  `run_all` passes over the resulting index;
- a second run of the same manifest does not duplicate chunks;
- a `local_path` escaping the data directory is rejected;
- a manifest SHA-256 mismatch aborts with nothing embedded or written;
- a dimension mismatch halts; the gate blocks a deliberately constructed superseded citation.
