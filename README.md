# Kanon

Authority-aware retrieval over real NRC regulatory documents. Spec and rationale: [KANON.md](KANON.md).

Thesis: authority-correct retrieval can't be done by recency ranking. Supersession is partial,
cross-family, and effective dates differ from issue dates (10 CFR 50.59's 1999 revision was
published 1999-10-04 and took effect 2001-03-13). It needs a bi-temporal supersession graph,
enforcement at the tool interface, and abstention when authority can't be resolved.

## Layout

| Layer | Module | What it does |
|---|---|---|
| 1 | `kanon/store.py` | Bi-temporal SQLite store + deterministic `resolve_authority` |
| 1 | `kanon/supersession.py` | Verbatim supersession text → typed edges (regex, no LLM) |
| 2 | `kanon/ingest.py` | Docling parse → page-anchored chunks with full metadata |
| 2 | `kanon/retrieval.py` | Hot/cold Chroma + BM25, RRF fusion, modes `none`/`flag`/`graph` |
| 3 | `kanon/tools.py` | Typed tools; `as_of` required; no unscoped search tool |
| 3 | `kanon/agent.py` | LangGraph agent with the Tier B gate as a blocking node |
| 3 | `kanon/trace.py` | Callback → human-readable trace per query |
| 4A | `kanon/checks.py` | Pipeline integrity assertions (halt, never self-repair) |
| 4B | `kanon/agent.py::check_citations` | Authority gate: deterministic, blocking in mode `graph` |
| 4C | `kanon/judge.py` | LLM judge — advisory annotations only, cannot alter answer or gate |
| eval | `kanon/evals.py`, `evals/questions.yaml` | Three-mode comparison; superseded-citation rate always paired with answer rate |

**Requirements vs records.** Requirements state what governs (CFR sections, reg guides, tech
specs, amendments) and are subject to the authority gate. Records state what happened on a
date — inspection reports, enforcement actions, Federal Register rules and notices — and are
searched only by `find_precedent_events`, exempt from the governing check. The CFR is the
codified law; the Federal Register is its change log. Unknown kinds default to requirement.

## Run

```bash
uv sync
cp .env.example .env               # set OPENAI_API_KEY
uv run kanon load                  # data/manifest.jsonl + labels -> authority.db
uv run kanon ingest                # parse, chunk, embed, Tier A checks
uv run kanon resolve 10CFR50.59 --as-of 2000-06-01
uv run kanon ask "An engineer wants to replace a primary coolant pump..." --mode graph
uv run kanon-eval                  # all three modes; --retrieval-only needs no chat model
uv run pytest -q
```

## Known limitations

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
  and both versions govern; it doesn't know *which sections* (that's in `scope_note` prose).
- Not in the public record, so not in this corpus: controlled drawings, design calculations,
  maintenance history, and plant procedures such as MP-033.05 (only referenced by inspection reports).
