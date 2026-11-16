# Security and integrity review

Reviewed 2026-11-16. This review covers the local ingest command, retrieval filters, and the
authority gate. Everything runs in one local process: no database server, no HTTP service, no
network listener. The Postgres ingest queue, its metadata sidecar and the localhost dashboard
this review originally covered were removed as out of scope (see KANON.md), which retired the
atomicity, shared-secret and image-pinning risks below with them.

## Fixed in this hardening pass

- Removed a populated ADAMS API key from the tracked `.env.example`. The key existed in commit
  history and must be revoked or rotated; replacing the working-tree value does not erase it.
- Manifest paths are resolved beneath `KANON_DATA_DIR`; absolute paths and symlink/path traversal
  outside that directory are rejected before parsing.
- Full SHA-256 values are streamed instead of loading whole files, and the digest is compared
  with the manifest's expected value before the file is parsed.
- Vector count, finite values, dimension, metadata, page, and hot/cold routing are checked before
  an upsert. Ingest halts on the first failure rather than continuing with a partial index.
- Facility filters fail closed for unknown applicability and allow global documents only when
  they are explicitly marked global. Filters apply before dense and BM25 ranking.
- A requirements query receives a requirements search even if the model omits the tool call.
  Explicit unit and docket values parsed from the question are forced into search calls, and a
  graph-mode answer that cites only historical records is blocked.

## Remaining risks

### High for untrusted uploads: PDF parsing has no resource sandbox

Docling processes PDFs in the ingest process. File headers, trailers, encryption, checksums, and
extracted text are checked, but there is no page-count/size quota, CPU/memory limit, parser
subprocess boundary, or hard parse timeout. A malformed or adversarial PDF can exhaust the
process. The corpus here is operator-curated and public; run parsing in a restricted
subprocess/container with size, page, memory, CPU and wall-clock limits before accepting
documents from untrusted users.

### Medium: retrieved text can influence the generation model

Source text is placed in the answer prompt. The deterministic authority and citation gates stop
unretrieved or non-governing citations, but they do not prove that a cited passage entails every
claim or prevent instructions embedded in a document from steering answer wording. Treat source
content as untrusted data, add claim-to-passage checks for release-sensitive use, and keep the
Tier C review advisory as documented.

### Medium: model providers receive corpus and question text

The current embedding and chat defaults use hosted OpenAI models. This corpus is public, but the
same configuration is unsuitable for controlled plant documents or sensitive user questions
without an approved data-handling agreement or self-hosted models.

### Low/medium: traces and error records retain sensitive detail

Trace files contain questions, answers, citations, and authority evidence, and `traces/timing.jsonl`
records per-stage timings. They are gitignored but have no retention policy or field-level
redaction. Set restrictive filesystem/database permissions and a retention policy
before ingesting non-public material.

### Supply-chain hardening

Python resolution is pinned in `uv.lock`, and there are no container images left to pin. Add
dependency vulnerability scanning before deployment.

## Verification

The suite covers path confinement, manifest checksum mismatch, re-run idempotency, hot/cold
routing, Tier A index integrity, structured metadata extraction, hard filter isolation, hybrid
identifier retrieval, authority gating, and the forced requirements-search invariant. See
VERIFICATION.md.

