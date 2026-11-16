# Kanon

Authority-aware retrieval over real NRC regulatory documents. Spec and rationale: [KANON.md](KANON.md).

Thesis: authority-correct retrieval can't be done by recency ranking. Supersession is partial,
cross-family, and effective dates differ from issue dates (10 CFR 50.59's 1999 revision was
published 1999-10-04 and took effect 2001-03-13). It needs a bi-temporal supersession graph,
enforcement at the tool interface, and abstention when authority can't be resolved.

Operational verification and security notes: [VERIFICATION.md](VERIFICATION.md) and
[SECURITY_REVIEW.md](SECURITY_REVIEW.md).

## Layout

| Layer | Module                                   | What it does                                                                   |
| ----- | ---------------------------------------- | ------------------------------------------------------------------------------ |
| 1     | `kanon/store.py`                         | Bi-temporal SQLite store + deterministic `resolve_authority`                   |
| 1     | `kanon/supersession.py`                  | Verbatim supersession text → typed edges (regex, no LLM)                       |
| 2     | `kanon/ingest.py`                        | Manifest → Docling parse → page-anchored chunks with full metadata → index     |
| 2     | `kanon/diff.py`                          | Deterministic CFR paragraph diff between two as-of dates (`kanon diff`)        |
| 2     | `kanon/retrieval.py`                     | Hot/cold Chroma + BM25, RRF fusion, modes `none`/`flag`/`graph`                |
| 3     | `kanon/tools.py`                         | Typed tools; `as_of` required; no unscoped search tool; `diff_revisions` (10 s budget) |
| 3     | `kanon/agent.py`                         | LangGraph agent with the Tier B gate as a blocking node                        |
| 3     | `kanon/trace.py`                         | Callback → human-readable trace per query                                      |
| 4A    | `kanon/checks.py`                        | Pipeline integrity assertions (halt, never self-repair)                        |
| 4B    | `kanon/agent.py::check_citations`        | Authority gate: deterministic, blocking in mode `graph`                        |
| 4C    | `kanon/judge.py`                         | LLM judge — advisory annotations only, cannot alter answer or gate             |
| eval  | `kanon/evals.py`, `evals/questions.yaml` | Three-mode comparison; superseded-citation rate always paired with answer rate |

**Requirements vs records.** Requirements state what governs (CFR sections, reg guides, tech
specs, amendments) and are subject to the authority gate. Records state what happened on a
date — inspection reports, enforcement actions, Federal Register rules and notices — and are
searched only by `find_precedent_events`, exempt from the governing check. The CFR is the
codified law; the Federal Register is its change log. Unknown kinds default to requirement.

Applicability filters accept plant, unit, and docket identifiers. Every supplied identifier must
match recorded facility metadata; unknown metadata is excluded. A document explicitly marked
`applicability_scope=global` remains eligible; the loader also assigns the closed `regulation`
kind global scope so facility filters cannot hide generally applicable 10 CFR requirements.
Applicability is taken from manifest/authority
fields or strict labeled first-page text, and unrecorded fields remain unknown. Chunk metadata in
Chroma carries these fields alongside revision, status, dates and source provenance. Both dense
and BM25 retrieval are filtered before reciprocal-rank fusion. A changed chunking
version requires re-ingestion before existing indexed documents gain this metadata.

## Run

```bash
uv sync
cp .env.example .env               # model-provider keys are needed only for embedding/answering
uv run kanon load                  # data/manifest.jsonl + labels -> authority.db
uv run kanon ingest                # parse, validate, embed, route hot/cold, Tier A checks
uv run kanon resolve 10CFR50.59 --as-of 2000-06-01
uv run kanon diff 10CFR50.59 --from 2000-06-01 --to 2026-11-15   # paragraph-level diff of what governed on each date
uv run kanon ask "An engineer wants to replace a primary coolant pump..." --mode graph
uv run kanon-eval                  # all three modes; --retrieval-only needs no chat model
uv run kanon-eval --modes none --rerank    # the reranker A/B
uv run pytest -q
```

No services: SQLite for authority, a local Chroma directory for vectors, files for traces. Ingest
is one process over `data/manifest.jsonl`, and it **halts on the first bad document** rather than
retrying it into a queue — a document that silently fails to index is a document the system will
silently fail to cite. Re-running is an upsert, not a duplicate: chunk IDs are deterministic and a
revision's old chunks are dropped before its new ones are written. The acceptance gates, in order,
are in [VERIFICATION.md](VERIFICATION.md).

A Postgres job queue (leases, `SKIP LOCKED` workers, retries, dead-letter table, audit trigger)
and a localhost timing dashboard were built here and then **removed**: KANON.md puts servers and
operational surface out of scope, and 26 documents parse in minutes on one laptop. Bring them back
when ingest is a service with concurrent producers, not a command someone runs.

## What a run costs, and the two guards on it

Every paid request goes through `kanon/llm.py`, so both guards live there (`kanon/ratelimit.py`).

- **Sliding window** (`KANON_MAX_REQUESTS_PER_MIN`, default 60): at most that many requests in any
  trailing 60 s, chat and embeddings sharing one counter. Paces a run and stops a runaway agent
  loop. Rejected `langchain_core.rate_limiters.InMemoryRateLimiter`, which ships with the stack
  and is free: it is a token bucket, so a refilled bucket bursts past the limit a provider counts
  over a window. The sliding window's cost is one timestamp per in-window request.
- **Request budget** (`KANON_MAX_REQUESTS`, default 1500): a hard cap for one process. The run
  stops with `BudgetExceeded` before the request is sent. **This is the guard that protects the
  bill — pacing changes when requests happen, never how many.**

Where the money actually goes: a generation question is 3 chat calls (`classify_query`,
`select_tools`, `draft`) plus one embedding per search, so a three-mode sweep of 19 questions is
roughly 600 chat calls and 600 embeddings. The chat calls dominate by orders of magnitude —
`text-embedding-3-small` is not an LLM and bills ~$0.02/1M tokens, which puts the whole 3550-chunk
corpus ingest at a few cents. Reasoning tokens are billed as output and are invisible in the
response, which is why `KANON_REASONING_EFFORT` is **capped at `medium` for the Sol models**
(`llm.reasoning_effort`): a request for `high` is lowered, a request for `low` is honoured. Every
run prints the request count it used.

## Known limitations

- **Diff granularity is the designator, not the legal subject.** `kanon diff` keys paragraphs by
  their CFR designator, so a paragraph that was renumbered or restructured between revisions is
  reported as GONE plus NEW rather than CHANGED. In the 1996 -> 2025 diff of 10 CFR 50.59, `(c)`
  reads as GONE because its subject matter now lives in `(c)(1)`/`(c)(2)`. The output says so, and
  the quoted text is there to be compared. A paragraph whose text is identical but whose number
  moved cannot be detected at all without per-paragraph chunking.
- **Same-provider judge.** `RAG_MODEL` and `JUDGE_MODEL` both default to an OpenAI model, so the
  Tier C judge may share failure modes with the generator. Accepted for consistency; set
  `JUDGE_MODEL=anthropic:<model>` (one line) for cross-family independence. The judge is
  advisory only — it can never override the deterministic gate.
- **Page = page of the fetched PDF**, not the printed page label. HTML-native sources (eCFR, NRC
  web pages) are `paginated=false` and cite page 1 by convention.
- **Federal Register page bleed.** GovInfo per-document FR PDFs start and end on pages shared with
  other agencies' notices (SBA, FAA), so the first/last chunks of an FR record can carry a few
  paragraphs of unrelated text under the NRC doc id. Page ranges can't fix it; text markers
  (the NRC heading and the "[FR Doc. …]" line) could. Records only, never requirements.
- **Partial supersession is document-level.** The gate knows a document is partially superseded
  and both versions govern; it doesn't know _which sections_ (that's in `scope_note` prose).
- Not in the public record, so not in this corpus: controlled drawings, design calculations,
  maintenance history, and plant procedures such as MP-033.05 (only referenced by inspection reports).
