# Security and integrity review

Reviewed 2026-11-15. This review covers the local ingest queue, metadata sidecar, retrieval
filters, and authority gate. It assumes the Docker services remain bound to localhost as shown
in `docker-compose.yml`.

## Fixed in this hardening pass

- Removed a populated ADAMS API key from the tracked `.env.example`. The key existed in commit
  history and must be revoked or rotated; replacing the working-tree value does not erase it.
- Manifest paths are resolved beneath `KANON_DATA_DIR`; absolute paths and symlink/path traversal
  outside that directory are rejected before parsing.
- Full SHA-256 values are streamed instead of loading whole files. The worker compares the bytes
  with both the queued identity and the manifest's expected digest before parsing, after parsing,
  before each vector write, and before publication.
- Queue claims carry a generation fence. Progress, failure, and publication updates require the
  current worker, claim generation, live lease, and current document generation.
- Different content/status identities for the same document revision are serialized and older
  jobs are superseded. Failed cleanup deletes only vectors still tagged with that ingest identity.
- Vector count, finite values, dimension, metadata, page, and hot/cold routing are checked before
  an upsert. Postgres records lifecycle transitions through an append-only trigger.
- Facility filters fail closed for unknown applicability and allow global documents only when
  they are explicitly marked global. Filters apply before dense and BM25 ranking.
- A requirements query receives a requirements search even if the model omits the tool call.
  Explicit unit and docket values parsed from the question are forced into search calls, and a
  graph-mode answer that cites only historical records is blocked.

## Remaining risks

### High: Postgres and Chroma cannot publish atomically

An in-flight Chroma upsert may finish after the worker loses its Postgres lease or connection.
Deterministic IDs, ingest ownership tags, retries, and final integrity checks make recovery
repeatable, but they do not provide a distributed transaction or an atomic visibility switch.
For a shared production service, add a staging collection/index generation and switch a single
query-visible generation only after verification, or use a store that supports transactional
metadata and vectors together.

### High for untrusted uploads: PDF parsing has no resource sandbox

Docling processes PDFs inside the worker process. File headers, trailers, encryption, checksums,
and extracted text are checked, but there is no page-count/size quota, CPU/memory limit, parser
subprocess boundary, or hard parse timeout. A malformed or adversarial PDF can exhaust a worker
and will be attempted three times. Run parsing in a restricted subprocess/container with size,
page, memory, CPU, and wall-clock limits before accepting documents from untrusted users.

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

### Medium when services leave localhost: database/vector authentication

The development Postgres password is predictable and Chroma HTTP mode has no application-level
authentication configured here. The Compose ports bind to `127.0.0.1`, which limits the current
exposure. Any remote/shared deployment needs unique secrets, TLS, network policy, and Chroma
authentication or an authenticated proxy.

### Low/medium: traces and error records retain sensitive detail

Trace files contain questions, answers, citations, and authority evidence. Queue errors retain
bounded tracebacks and paths in Postgres. They are gitignored but have no retention policy or
field-level redaction. Set restrictive filesystem/database permissions and a retention policy
before ingesting non-public material.

### Supply-chain hardening

Python resolution is pinned in `uv.lock`, but container images use mutable tags rather than image
digests. Add dependency vulnerability scanning and pin reviewed image digests for deployment.

## Verification

The completed suite covers path confinement, manifest checksum mismatch, idempotency, legacy
schema migration, lease reclaim, stale claim fencing, newer-generation protection, audit events,
DLQ behavior, structured metadata extraction, hard filter isolation, hybrid identifier retrieval,
authority gating, and the forced requirements-search invariant.

