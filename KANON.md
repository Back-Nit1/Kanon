# Kanon — Authority-Aware Retrieval over Nuclear Regulatory Documents

## Context

I'm building this to learn how to engineer a retrieval system for a domain where
retrieval errors have real consequences. The motivating domain is nuclear
engineering and licensing: companies in this space build AI that nuclear
engineers use to answer design and compliance questions, and their core claim is
that output is _verified and traceable_ rather than inferred. I want to
understand firsthand what it actually takes to make that claim true.

This is partly preparation for a technical interview, so every design decision
here must be one I can explain and defend verbally. **If a piece of code can't be
explained out loud, cut it.** Explicable beats clever.

Project name: **Kanon**. Python package: `kanon`.

## The anchor scenario (real, verified)

Build toward answering this question well:

> An engineer wants to replace a primary coolant pump. What requirements
> currently govern that change, does it need prior NRC approval, and has anything
> gone wrong when this was done before?

This is grounded in a real documented failure — **NRC Enforcement Action
EA-98-155**, University of Michigan Research Reactor
(`https://www.nrc.gov/reading-rm/doc-collections/enforcement/actions/reactors/ea98155.html`):

- April 1996: modification Request No. 120 installed a new primary cooling pump
  and motor and removed the pump discharge check valve internals.
- A 10 CFR 50.59 safety evaluation _was_ prepared.
- It was inadequate: it did not consider the effect of increased flow on inlet
  temperature safety limits, and **did not identify that a technical
  specification change was required.**
- Result: increased primary coolant flow meant the limiting safety system setting
  for core outlet temperature could no longer assure the inlet temperature safety
  limit stayed below 116°F at 2 MW.

A pump change propagated into a thermal safety limit, through a compliance
evaluation that missed it, and an authority document needed updating and nobody
caught it. That single case spans both the regulatory-compliance problem and the
thermal-propagation problem.

**A second verified case worth including** — FitzPatrick inspection report
`ML22027A362`: a 'B' condensate booster pump seal was replaced with the wrong
seal style, causing a leak and a downpower to 84 percent. Root cause was that
maintenance procedure **MP-033.05, Revision 28** lacked clarity on which seal
style to use. A specific _revision_ of a document being wrong caused a physical
failure.

**Verified accession numbers** (fetch and read before citing anywhere):

| Accession     | What it is                                                                                    |
| ------------- | --------------------------------------------------------------------------------------------- |
| `ML22027A362` | FitzPatrick IR 05000333/2021004 — condensate booster pump seal, procedure Rev 28 root cause   |
| `ML15219A611` | FitzPatrick IR 05000333/2015007 — modifications team inspection, 19 separate 50.59 screenings |
| `ML12166A406` | FitzPatrick 2012 modifications team inspection — 20 separate 50.59 screenings                 |
| `ML083150382` | FitzPatrick IR 05000333/2008004 — 'B' feedwater pump inboard seal replacement                 |

The 50.59-screening-heavy team inspection reports are the densest corpus
material — each reviews ~20 modifications against the regulations in force at
that time.

**Unverified — do not cite without checking:** a Point Beach auxiliary feedwater
pump recirculation-line modification package. It may exist; it was not confirmed.
Either verify it via ADAMS or leave it out.

## The problem being solved

Nuclear plant documentation spans decades and is constantly revised. A license
amendment supersedes specific prior technical specification pages. A regulatory
guide revision supersedes an earlier revision — and sometimes supersedes a
_different_ guide entirely, or only portions of one.

When an engineer asks what requirements apply to replacing a pump, a conventional
RAG system retrieves semantically relevant text and answers confidently —
including from documents superseded years ago. The answer is fluent,
well-sourced, and wrong in a way that matters.

Critically, **this failure is invisible to standard RAG evaluation.** Faithfulness
scores high (the answer genuinely is grounded in the retrieved text). Context
precision scores high (the chunks genuinely are topically relevant). Every metric
reads green while the system produces a licensing problem.

## The thesis

> Authority-correct retrieval cannot be achieved by recency ranking. In
> regulatory corpora, supersession is partial, cross-family, and occasionally
> runs backwards against publication date (effective dates differ from issue
> dates). It requires an explicit bi-temporal supersession graph, enforcement at
> the tool interface, and abstention when authority cannot be resolved.

Success is a **measured** before/after on real NRC documents, not an assertion.

## Hard constraints

1. **Real data only.** Every document comes from NRC ADAMS, eCFR, or another
   public primary source. No synthetic documents, no fabricated revision
   histories, no invented accession numbers. If a needed document isn't
   available, handle its absence — don't fake it.
2. **Never delete or discard superseded documents.** They are required for
   point-in-time queries and for the supersession chain that makes a citation
   auditable. Superseded documents are _tiered_, never evicted.
3. **Abstention is a valid output.** When authority can't be resolved, refuse and
   name the specific unresolved supersession edge.
4. **Authority checks are deterministic.** Whether a document currently governs
   is a lookup against recorded metadata, never a model's judgment.
5. **No unverified claims.** Any accession number, plant name, or case example
   appearing in output or docs must have been fetched and read. Mark anything
   unverified explicitly.
6. **Every metric reported with its counter-metric.** See Evaluation.

---

# Tool stack — decisions and the tradeoffs behind them

Each decision below has a rejected alternative and a reason. Keep these comments
in the code; they are as much the deliverable as the implementation.

## Document parsing: **Docling**

- **Chosen because:** MIT licensed, runs locally, best-in-class multi-column
  reading order (~94% in published comparisons), and it reliably preserves page
  numbers. Page anchoring is a hard requirement here, and in one published
  benchmark Unstructured dropped page numbers entirely on a table-of-contents
  test — disqualifying for this use case.
- **Domain argument worth stating out loud:** in nuclear, controlled documents
  frequently cannot be sent to a third-party cloud parser. A self-hostable parser
  is the defensible choice even where a hosted one scores higher.
- **Rejected:** LlamaParse — more accurate on dense/complex tables (vision-model
  per page) and genuinely better on hard scans, but hosted, per-page billing, and
  cloud-only. Reconsider only if table extraction becomes the bottleneck.
- **Rejected:** Unstructured — broadest format support (30+), but weaker on the
  specific thing we need (page-number fidelity) and markedly slower.

## Authority graph: **hand-rolled bi-temporal store in SQLite**

- **Chosen because:** the supersession edges here come from _explicitly cited
  text_ in NRC documents, not from inference. A deterministic parse into a
  deterministic store is the whole point of the thesis.
- **Rejected, but adopt its model:** **Graphiti** (Zep's open-source temporal
  knowledge graph, arXiv 2501.13956) is the closest prior art. Its bi-temporal
  edge model — tracking when a fact was true separately from when it was
  ingested, and setting an `invalid_at` timestamp on outdated edges _rather than
  deleting them_ — is exactly the right conceptual model, and this repo adopts
  it. It was rejected as an implementation because (a) its entity and edge
  extraction is LLM-driven, which violates constraint #4; (b) it requires Neo4j
  or FalkorDB, which is infrastructure this project doesn't need; (c) it's
  designed for agent memory built from conversational episodes, not for explicit
  supersession statements in formal documents.
- **Be able to say this in one sentence:** "I took Graphiti's bi-temporal model
  and rejected its extraction approach, because authority has to be a lookup."

## Vector store: **Chroma**, two collections

- **Chosen because:** trivial local persistence, metadata filtering, and multiple
  named collections — which is all the hot/cold tiering needs at this scale.
- **Rejected:** Qdrant — better filtered-search performance at scale, but the
  corpus here is a few thousand documents and the added operational surface buys
  nothing measurable.

## Embeddings: **OpenAI `text-embedding-3-small`** for v1

- **Chosen because:** cheap, good enough, one less variable while the authority
  layer is being built.
- **Stretch goal, high value if time allows:** `atomic-canyon/fermi-1024`, a
  nuclear-domain sparse retrieval model trained on ADAMS with a nuclear-specific
  tokenizer (terms like "NRC" are single tokens rather than being split). A
  measured comparison of a general embedding model against a domain-specific one
  on this corpus is a genuinely interesting result. Note it's a SPLADE-style
  sparse model, so it needs its own integration path — treat as v2.

## Reranking: **implemented but off by default; A/B it in the baseline**

> **Status (v1):** built as `kanon/rerank.py` (`CrossEncoderReranker`, BGE via `transformers` —
> `torch`/`transformers` already ship with docling, so no new dependency). Wired into
> `CorpusIndex._hybrid`: with a reranker attached, RRF becomes a recall stage that keeps
> `RERANK_CANDIDATES = 100` metadata-pre-filtered candidates and the cross-encoder picks the
> final k. **Off unless `KANON_RERANK_MODEL` is set**, for the reason below. `kanon-eval --rerank`
> measures every requested mode twice (`<mode>` and `<mode>+rerank`) through the production path.

- **Reasoning:** reranking is the single largest quality gain available in a RAG
  stack short of a model upgrade (published figures: 15–25 points of top-1
  precision over dense-only). But it is **orthogonal to this thesis** — a
  reranker scores topical relevance, and a superseded revision is topically
  near-identical to the current one.
- **Hypothesis worth actually testing:** adding a reranker _increases_ the
  superseded-citation rate in mode `none`, because a better ranker is better at
  surfacing the near-duplicate superseded text. If that holds, it's a
  counterintuitive, defensible finding: improving relevance makes authority worse.
  Cheap to test — run mode `none` with and without.
- **If adding one:** `BAAI/bge-reranker-v2-m3` (MIT, ~0.6B, self-hostable,
  the common open default). Rejected Cohere Rerank — stronger managed default,
  but hosted and per-query billed. Rejected ColBERTv2 — late interaction is
  genuinely well-suited to long documents where the relevant clause is buried in
  thousands of tokens, which describes this corpus, but it needs per-token
  embeddings (~30KB/doc vs ~3KB) and a PLAID/IVF index. Too much infra for the
  return here.
- **Gotcha if you do add one:** reranker scores are not comparable across models.
  A 0.7 threshold means completely different things for BGE (sigmoid, relevant
  pairs ~0.55–0.75) versus Cohere (~0.85–0.95) versus ColBERT (unbounded MaxSim).
  Never hardcode a cross-model threshold.

## Agent framework: **LangGraph** (`StateGraph`)

- **Chosen because:** the authority gate must be a blocking node with power to
  route to abstention, and the explicit graph is what produces a readable trace.
- **Rejected:** LangChain `AgentExecutor` / prebuilt ReAct agents — no place to
  insert a hard gate inside the loop.
- **Rejected:** a plain tool-calling loop against the API directly — fine, and
  fewer dependencies, but no built-in callback/trace infrastructure.

## Models

Config via env, never hardcoded:

```
RAG_MODEL=gpt-5.6-sol          # OpenAI, generation path
JUDGE_MODEL=gpt-5.6-sol        # see note
EMBEDDING_MODEL=text-embedding-3-small
```

**Note on the judge:** GPT-5.6 Sol is an OpenAI model, so using it for both paths
means the judge is not cross-family independent — it may share failure modes with
the model it's grading. This is an accepted tradeoff, not an oversight; write it
in the README so it reads as a known limitation rather than a blind spot. If
independence matters more than consistency, point `JUDGE_MODEL` at a different
provider. Build a thin provider-agnostic wrapper so this is a one-line change.

## Claude Code skills to use

Use **`pdf-reading`** for the document ingest leg — the corpus is NRC PDFs and
that skill covers extraction strategy, page rasterization, and choosing a reading
approach per document type.

Use **`dataviz`** when generating charts for the eval report.

(Note: no skills named "Graphify" or "Ponytail" exist in this environment.
Graphiti is a Python library, covered under the authority-graph decision above.)

---

# Architecture

```
┌─────────────────────────────────────────────────────────┐
│ 4. Verification harness                                  │
│    pipeline assertions │ authority gate │ LLM judge      │
├─────────────────────────────────────────────────────────┤
│ 3. Agent (LangGraph)                                     │
│    authority-typed tools, explicit blocking gate node    │
├─────────────────────────────────────────────────────────┤
│ 2. Retrieval                                             │
│    hybrid BM25 + dense, hot/cold collections             │
├─────────────────────────────────────────────────────────┤
│ 1. Bi-temporal authority store (deterministic, SQLite)   │
└─────────────────────────────────────────────────────────┘
```

**Why layer 1 is deterministic:** whether a revision currently governs is a
legal/record fact. Having a model infer it would be the exact error the system
exists to prevent.

**Why layer 3 types its tools:** a prompt instruction ("prefer current
revisions") is soft and can be ignored. A tool signature that _requires_ an
`as_of` date makes the unsafe query inexpressible.

**Why layer 4 splits by check type:** using an LLM to check whether embeddings
were created is indefensible; using an assertion to check whether an answer
overclaims is impossible. Match the detector to the failure.

## Layer 1 — Bi-temporal authority store

Two time axes, following Graphiti's model. This is what handles the
effective-date-precedes-issue-date case that defeats recency ranking.

```sql
CREATE TABLE documents (
  doc_id          TEXT NOT NULL,   -- 'RG-1.192', 'MP-033.05', amendment id
  revision        TEXT NOT NULL,
  family          TEXT,            -- grouping key for cross-family supersession
  title           TEXT,
  -- valid time: when this governed in the world
  effective_from  DATE,
  effective_to    DATE,            -- NULL = still governing
  -- record time: when we learned it
  issue_date      DATE,            -- when published; MAY be after effective_from
  ingested_at     TIMESTAMP NOT NULL,
  status          TEXT NOT NULL,   -- 'active' | 'superseded' | 'withdrawn'
  accession       TEXT,
  source_url      TEXT,
  PRIMARY KEY (doc_id, revision)
);

CREATE TABLE supersession_edges (
  from_doc    TEXT NOT NULL,   -- the superseding document
  from_rev    TEXT NOT NULL,
  to_doc      TEXT NOT NULL,   -- the superseded document
  to_rev      TEXT NOT NULL,
  edge_type   TEXT NOT NULL,   -- 'full'|'partial'|'withdrawn'|'cross_family'
  scope_note  TEXT,            -- for 'partial': which portion
  valid_from  DATE,            -- when the supersession took effect
  ingested_at TIMESTAMP NOT NULL,
  evidence    TEXT NOT NULL,   -- verbatim source text the edge was parsed from
  confidence  TEXT NOT NULL    -- 'explicit'|'inferred'|'unresolved'
);
```

`evidence` and `confidence` are mandatory. An edge with `confidence='unresolved'`
is what triggers abstention — the system must say _why_ it can't resolve.

**Resolver:**

```python
def resolve_authority(doc_id: str, as_of: date) -> AuthorityResult:
    """Documents governing `doc_id`'s subject matter on `as_of`.

    Returns:
      authoritative: list[(doc_id, revision)]   # >1 under partial supersession
      chain:         list[Edge]                 # full traversal, for traceability
      unresolved:    list[Edge]                 # edges blocking a confident answer
    """
```

It returns a **list**. Partial and cross-family supersession means one document's
subject matter can be governed by several successors. Any design assuming a
single current version is wrong.

## Layer 2 — Retrieval

**Hot/cold collections** (this replaces any eviction scheme):

- **hot** — chunks whose parent document has `status='active'`
- **cold** — chunks from superseded/withdrawn documents, fully retained

Default queries hit hot only. Time-anchored queries search both and resolve
through the graph. Rationale: a `where={"status":"active"}` filter is one
refactor away from deletion and nothing visibly breaks — you just quietly start
citing dead documents. A separate collection makes unsafe documents unreachable
from the default code path. Secondary benefit: superseded revisions are
near-identical to current ones, making them the worst possible ranking
distractors; removing them from the default index improves ranking, not just
speed.

**Stack:** Chroma (two collections), `rank_bm25` via LangChain `BM25Retriever`,
fused with `EnsembleRetriever` (reciprocal rank fusion).

**Chunk metadata — every chunk, no exceptions:**

```python
{
  "doc_id": str, "revision": str, "status": str,
  "effective_from": str, "effective_to": str | None, "issue_date": str,
  "section": str, "page": int,     # page REQUIRED
  "accession": str, "source_url": str,
}
```

`page` is required because citation granularity is half of citation integrity. A
citation naming the correct revision of a 200-page NUREG is not auditable. If a
chunk has no page number, it's invalid.

## Layer 3 — Agent

**Graph:**

```
classify_query ──> select_tools ──> retrieve ──> resolve_authority
                                                       │
                                                   validate
                                                  /         \
                                            answer          abstain
```

- `classify_query` — current-state or time-anchored? Extract any `as_of`. Use
  `with_structured_output` on a Pydantic model; never parse free text.
- `resolve_authority` — deterministic graph walk over every retrieved chunk's
  parent document
- `validate` — the blocking gate (layer 4, tier B)
- `answer` / `abstain` — terminal nodes

**Tools, with Pydantic `args_schema`:**

```python
@tool(args_schema=CurrentRequirementsArgs)
def search_current_requirements(query: str, as_of: date) -> list[Chunk]:
    """Search only documents governing on `as_of`. `as_of` is REQUIRED."""

@tool(args_schema=RevisionHistoryArgs)
def get_revision_history(doc_id: str) -> list[DocumentVersion]:
    """Full revision chain, newest first, with status per revision."""

@tool(args_schema=ResolveSupersessionArgs)
def resolve_supersession(doc_id: str, revision: str) -> AuthorityResult:
    """What superseded this, how, and what governs now."""

@tool(args_schema=PrecedentArgs)
def find_precedent_events(system: str, symptom: str, since: date | None) -> list[Chunk]:
    """Search operating experience (inspection reports, event reports) for prior
    occurrences. Searches the historical record intentionally — superseded status
    does NOT disqualify an operating-experience document, because such a document
    records what happened rather than stating what governs."""
```

Design notes to preserve in comments:

- `as_of` has **no default**. The agent must commit to a date. That's the
  enforcement.
- There is deliberately **no** unscoped `search_all_documents` tool. Removing it
  is the point.
- `find_precedent_events` is the deliberate exception to the authority rule.
  Comment it clearly — an interviewer will ask why this tool breaks the rule.

**Tracing:** a `BaseCallbackHandler` recording every tool call with arguments,
every retrieved chunk id, and every authority resolution, written to a structured
trace file per query. **That trace is the traceability chain** — format it for a
human reviewer, not as debug output.

**Citation extraction:** `with_structured_output` against:

```python
class Citation(BaseModel):
    doc_id: str
    revision: str
    section: str | None
    page: int
```

The LLM extracts citations from its own answer. It does **not** judge whether
they're authoritative.

## Layer 4 — Verification harness

### Tier A — Pipeline integrity (deterministic, no LLM)

Inline. On failure: **halt, surface the error, do not self-repair.**

- every chunk carries all required metadata keys
- embedding count == chunk count after ingest
- embedding dimensionality matches configured model
- round-trip: a known chunk is retrievable by its own text
- hot collection contains zero chunks with `status != 'active'`
- no chunk lacks a `page` value

### Tier B — Authority gate (deterministic, no LLM)

The blocking `validate` node. For every citation:

1. Look up the cited `(doc_id, revision)`.
2. Current-state query + citation superseded/withdrawn → **block, abstain.**
3. Time-anchored query + citation not governing on `as_of` → **block.**
4. Any `unresolved` edge bearing on a citation → **block.**

Abstention must name the document, revision, and unresolved edge. "I don't know"
is unacceptable; "I cannot confirm whether RG X Rev N was superseded by Y — the
supersession record is ambiguous" is correct.

### Tier C — Answer judge (LLM, advisory only)

Reviews the completed trace. **Advisory: annotates, never blocks, never edits.**
Tier B holds all blocking authority — an LLM must not be able to override a
deterministic check.

Checks: does the answer overclaim relative to evidence; does it answer a
different question; are there uncited claims; does the reasoning skip a step.
Output: structured annotations appended to the trace.

---

# Data sources

**ADAMS Public Search API** (free self-serve key, no approval wait)

- Register: `https://adams-api-developer.nrc.gov/` → Products → ADAMS Public
  Search API → Subscribe. Key from env `ADAMS_API_KEY`.
- `POST https://adams-api-developer.nrc.gov/aps/api/search`
- `GET  https://adams-api-developer.nrc.gov/aps/api/search/{accessionNumber}`
- Has a content toggle returning indexed document text
- Covers the Public Library and the Public Legacy Library

**eCFR** (no auth)

- `GET https://www.ecfr.gov/api/versioner/v1/versions/title-10.json`
- `GET https://www.ecfr.gov/api/versioner/v1/full/{date}/title-10.xml?part=50`
- **Known limit: history only goes back to January 2017.** For earlier
  point-in-time text use GovInfo annual CFR editions
  (`govinfo.gov/bulkdata/CFR/{year}/title-10/`). Do not build an `as_of=1996`
  path — which the anchor scenario needs — on eCFR alone.

**NRC Regulatory Guide division index pages**

- Publish revision number, issue date, status (`W` = withdrawn), and explicit
  supersession history strings in prose
- Scrape → parse into typed edges. Free ground-truth label set.
- Parser must store the verbatim source string as `evidence`

**Operating experience** (for `find_precedent_events`)

- NRC enforcement actions (the anchor case EA-98-155 lives here)
- LERSearch: `https://lersearch.inl.gov/`
- NRC Event Notification Reports — yearly raw pipe-delimited files
- Inspection reports via ADAMS (see verified accession table above)

**Corpus target:** the FitzPatrick 50.59 team inspection reports plus the
associated technical specifications and license amendments, scoped to pump
systems. A few hundred to a few thousand documents. **Corpus size is not what's
being evaluated** — do not chase volume.

Fallback: if amendment supersession relationships prove unextractable from safety
evaluation prose, use the regulatory guide corpus for labels and report that the
fallback happened.

**Not available — do not claim it:** controlled drawings, design calculations,
and maintenance history are not in the public ADAMS record. Inspection reports
_reference_ them without publishing them.

---

# Evaluation

## Primary metric and its mandatory partner

**Superseded-citation rate** — fraction of answers citing at least one document
that did not govern at the query's effective date.

**Always reported alongside answer rate** (fraction answered rather than
abstained). A system that abstains on everything scores a perfect 0% and is
useless. Report them as a pair, in every report, without exception.

## Three comparison modes

Same frozen question set against all three. This comparison is the central result.

1. `none` — hybrid retrieval, no authority filtering (baseline)
2. `flag` — boolean `status='active'` metadata filter
3. `graph` — full bi-temporal resolver with typed edges

Mode 2 exists to show the obvious fix is insufficient. Find cases where `flag`
fails and `graph` succeeds: partial supersession, cross-family supersession, or
effective-date-precedes-issue-date.

**If no such case exists in the corpus, say so in the report rather than
asserting one.** Do not construct one.

## Optional extra run

Mode `none` with and without a reranker, to test whether better relevance
ranking _increases_ the superseded-citation rate. Report either way.

## Tiers

- **Retrieval metrics** on scraped ground truth: recall@k, MRR,
  superseded-chunk rate in the retrieved set
- **Frozen question set**: 20–25 version-sensitive questions with expected
  authoritative answers, in `evals/questions.yaml`. Once written, **pinned** —
  editing it mid-run invalidates comparison.
- **Domain sanity**: optionally run FermiBench (`atomic-canyon/FermiBench`, a
  nuclear IR benchmark built from ADAMS) to confirm authority filtering didn't
  degrade general retrieval. Sanity check, not headline.
- **Ragas** (optional): faithfulness and context precision. Pin the version — the
  legacy metric API is being deprecated. **Expect these to score high on wrong
  answers in mode `none`.** That result is itself worth reporting: it demonstrates
  why standard tooling misses this failure mode.

---

# Testing

`pytest`. Tests are the evidence the pipeline does what's claimed.

**Unit:**

- chunker output carries every required metadata key, including `page`
- supersession parser: fixture strings → expected typed edges (include a
  partial-supersession and a cross-family case)
- `resolve_authority` returns the correct set for known `as_of` dates
- `resolve_authority` returns multiple documents under partial supersession
- **bi-temporal case: `effective_from` earlier than `issue_date` resolves
  correctly and is not reordered by date sorting**

**Integration:**

- ingest fixture set → embed → assert vector count == chunk count and
  dimensionality matches config
- round-trip retrieval returns a known chunk by its own text
- hot collection query returns zero non-active chunks (assert, don't sample)
- full pipeline on a frozen question either answers or abstains, and **never**
  emits a superseded citation in current-state mode
- authority gate blocks a deliberately-constructed superseded citation
- Tier C annotations do not alter the answer or the gate decision

**Fixtures:** real documents, trimmed. Small real documents, never fabricated.

---

# Build order

Each step ends in something runnable. Don't advance past a failing step.

1. **Verify access (do first).** ADAMS key working, one search, one content
   fetch. eCFR version list for 10 CFR 50. Scrape one RG division index and parse
   its supersession strings. Fetch `ML22027A362` and `EA-98-155`. If any fails,
   stop and report.
2. **Authority store.** Schema, ingest from scraped labels, resolver + unit
   tests. Bi-temporal cases covered. No retrieval yet.
3. **Corpus ingest.** Docling parse with page numbers, chunk with full metadata.
   Tier A assertions green.
4. **Hybrid retrieval + baseline measurement.** Hot/cold collections. Run mode
   `none` and **record the superseded-citation rate before building the fix.**
   Measured, not estimated.
5. **Modes `flag` and `graph`.** Comparison harness across all three.
6. **LangGraph agent.** Typed tools, gate node, abstention, trace callback.
7. **Tier C judge.** Advisory annotations.
8. **Eval run + report.** Metric pairs, three modes, named failure cases.

# Out of scope — and why

Decisions, not omissions. Each should be explainable.

- **Document deletion / eviction / TTL** — destroys point-in-time capability and
  the audit chain. Tiering solves the performance concern without the loss.
- **Sharding** — premature at this corpus size; complicates reads for no measured
  benefit. Revisit when a measurement shows a bottleneck.
- **OCR of degraded legacy scans** — real and relevant, but a time sink. Restrict
  the corpus to documents with a text layer. OCR strategy is design discussion,
  not implemented capability.
- **Engineering drawing / P&ID parsing** — controlled drawings aren't in the
  public record. Do not claim this.
- **Physics simulation** — out of scope. Any "physics-informed" validation is a
  constraint/sanity-check layer at most, and only if actually implemented.
- **Real-time streaming, servers, health checks, multi-tenancy, auth** — this is
  a local evaluation harness, not a deployed service.
- **UI** — a CLI taking a question and printing the answer, the trace, and the
  gate decision. Nothing more.

# Deliverables

- working pipeline, three modes, runnable from CLI
- `authority.db` populated with bi-temporal documents and typed edges
- `evals/questions.yaml` (pinned) and an eval report with metric pairs
- `REPORT.md`: measured results, the named case where `flag` fails and `graph`
  succeeds (or an explicit statement that none was found), the rejected-tool
  tradeoffs, and known limitations including the same-provider judge
- passing test suite
- one trace file from a query that **abstained**, as the example output

# Final note

Write code that can be explained line by line. Where a decision was a tradeoff,
leave a comment naming the alternative and why it lost. Those comments are as
much the deliverable as the code.
