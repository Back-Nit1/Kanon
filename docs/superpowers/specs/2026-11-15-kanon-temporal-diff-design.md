# Kanon: temporal differencing (design)

Status: proposed, 2026-11-15. Size: small change (3 additions over existing Layer 1–3).

## Intent

Kanon's claim becomes point-in-time *differencing*, not just point-in-time retrieval: name the
exact paragraph of a regulation that governed on one date and does not govern on another, show
what ended it, and refuse — visibly — when an answer cites the wrong revision.

Why: the resolver already decides *which revision* governed on a date (`store.resolve_authority`,
eval: 0% superseded citations in mode `graph` against 47%/63% baselines). A reader of that output
still cannot see *what actually changed* in the text, which is the question an engineer asks
("what did 50.59 require in June 2000 that it doesn't require now?"). The data for it is already
indexed: six 10 CFR 50.59 revisions with full text.

## Goals

1. `kanon diff DOC --from DATE --to DATE` — paragraph-level gone / new / changed between the
   revisions that governed on each date, with page anchors and the supersession edge that ended
   the old one.
2. A blocked answer explains *what is actually out there*: the governing revision for the asked
   date, and the diff lines for the paragraph the model cited.
3. Every citation in an answer carries three developer-facing facts: the date it was resolved
   against, what that text supersedes, and whether it is a governing requirement or a historical
   record.
4. A new Tier B gate rule, `paragraph_not_in_revision`: if a citation names a paragraph/section
   designator, that designator must exist in the cited revision's paragraph map. Closes the two
   validation gaps found on 2026-11-15 — `Citation.section` was declared, printed and checked by
   nothing, and `not_retrieved` proves a page was retrieved but never that the cited paragraph is
   on it.

## Non-goals

- Physics, plant simulation, or any engineering calculation.
- A new eval question type (user declined; existing qa/resolver sets stay pinned and untouched).
- Re-ingest or re-embedding of the corpus.
- Semantic "did the meaning change" judgment. Diff is textual and deterministic; no LLM in it.

## Anti-goals (failure even if it runs)

- A diff that silently invents paragraph keys, or attributes text from §50.58/§50.60/§50.61 to
  §50.59. The 1996 CFR-edition chunks contain all four sections; leakage would be a wrong legal
  claim, not a cosmetic bug.
- An LLM anywhere in the authority or diff path.
- A diff that reads plausible but can't be audited: every line must carry doc_id, revision, page.

## Constraints

- Docling `section` metadata cannot key paragraphs: the 1996 revision's chunks are labeled
  `"10 CFR 50.59 Changes, tests, and experiments (CFR edition revised as of 1-1-97)"` and
  `"Nuclear Regulatory Commission"`. Paragraph designators exist only inside the chunk text.
- Only 10 CFR 50.59 has text across revisions (6 revisions, 5–10 chunks each). Reg guides are
  metadata-only, so `kanon diff RG-1.187` must say so rather than return an empty diff.
- Chunks are token-budget splits, not paragraph splits: one paragraph can span two chunks and a
  chunk can start mid-paragraph.
- Dates with no recorded revision (pre-1996-08-28) must report "nothing governed", consistent
  with the existing q02 abstain.

## Approaches considered

| Approach | Trade-off | Verdict |
| --- | --- | --- |
| **Regex over indexed chunk text** — pull designators `(a)`, `(a)(1)`, `(c)(2)(i)` from text already in Chroma, scoped to the §50.59 heading region | ~40 lines, deterministic, no re-ingest, works on today's index. Ceiling: a paragraph split across chunks is joined by designator order; a renumbered-but-identical paragraph reads as removed + added | **Chosen** (user) |
| Re-parse PDFs into one chunk per paragraph | Cleanest keys, exact per-paragraph page anchors. Costs `CHUNKING_VERSION` bump, full re-ingest, re-embedding of ~3.5k chunks | Rejected: cost without a thesis gain |
| Chunk-level text diff, no paragraph keys | Smallest diff. Output says "a chunk changed" instead of naming `(a)(2)` — which is the product | Rejected |

Surfaces (user picked all three): CLI, abstain message, agent tool. Agent tool gets a **10 s
wall-clock timeout** so a model call can't hang the graph.

## What gets built

**`kanon/diff.py`** (new, deterministic, no model)

- `section_region(text, section="50.59") -> str` — keep only the span from a `§ 50.59` heading to
  the next `§ 50.NN` heading. Drops §50.58/§50.60/§50.61 leakage. Text with no 50.59 heading at
  all contributes nothing.
- `paragraphs(chunks) -> dict[str, Paragraph]` — walk chunk text in page/chunk order, track the
  current designator path so a bare `(2)` inherits its parent `(a)`, and accumulate body text per
  key. `Paragraph` = key, text, page, doc_id, revision.
- `diff_paragraphs(old, new) -> (gone, new_, changed)` — set difference on keys; `changed` is keys
  in both whose whitespace/quote-normalized text differs.
- `diff_as_of(index, store, doc_id, from_date, to_date) -> DiffResult` — resolve the governing
  revision on each date via `store.resolve_authority`, load each one's chunks, diff them, and
  attach the ending supersession edges from `store.edges_into` for the old revision. Carries
  `unresolved`/`undated` straight through, so an ambiguous record is reported, never papered over.

**`kanon/retrieval.py`** — `CorpusIndex.chunks_for(doc_key) -> list[Chunk]`: all chunks of one
revision from hot + cold, ordered by page then id. Needed because every existing read path is a
ranked search.

**`kanon/store.py`** — `edges_out_of(doc_id, revision, known_at=None) -> list[Edge]`: what this
revision superseded. The store currently indexes only edges *into* a document; this is the other
direction, for "what does the cited text supersede".

**`kanon/agent.py`**
- `citation_notes(store, citations, as_of) -> list[str]`: per citation — `requirement` vs
  `record` (via `is_record`), the governing window (`effective_from`..`effective_to`), the
  as_of it was checked against, and `supersedes: ...` from `edges_out_of`. Printed under
  `Sources:` in the `answer` node and in the trace.
- `abstain` node: for each `superseded` / `not_governing_on_as_of` violation, append what governs
  on `as_of` and the diff lines for the paragraph keys the cited revision and the governing
  revision disagree on. This is the "explain why there was a blockage" requirement.
- `check_citations`: new rule `paragraph_not_in_revision`. When `Citation.section` parses to a
  designator (`(a)`, `(a)(2)`, `50.59(c)(2)(i)`, `§ 50.59(a)`), look it up in that revision's
  paragraph map; absent designator = violation naming the designators the revision does have.
  A citation with no parseable designator is NOT a violation — the model is not required to cite
  at paragraph level, only to be right when it does. Classed as an **integrity rule**, not an
  authority rule: it blocks in every mode, because a paragraph that does not exist in the cited
  text is ungrounded regardless of which revision governs, and the baselines' failure mode we are
  measuring is wrong-revision, not invented-paragraph. Needs the paragraph map inside the gate, so
  `check_citations` takes a `paragraphs: Callable[[str, str], dict[str, Paragraph]] | None`
  resolver (None = skip the rule, keeping the function pure and unit-testable without an index).

**`kanon/tools.py`** — `diff_revisions(doc_id, as_of_from, as_of_to)`, both dates required, no
defaults, same as `as_of` elsewhere. Runs `diff_as_of` in a worker thread with a 10 s timeout;
on timeout it returns a typed "diff timed out" result rather than raising into the graph.

**`kanon/cli.py`** — `kanon diff DOC --from DATE --to DATE`.

**`tests/test_diff.py`** — section scoping drops §50.60 text; bare `(2)` inherits `(a)`;
gone/new/changed on the real 1996 vs 2025 paragraph sets; metadata-only doc reports no text;
timeout path returns the typed result.

**`tests/test_agent.py`** (existing file) — `paragraph_not_in_revision` fires for a designator
absent from the cited revision, stays silent for one present, stays silent when `Citation.section`
is None or unparseable, and blocks in mode `none` as well as `graph`.

## Left to the builder

- Exact regex shape and the normalization used for `changed` (quote style, whitespace, `§` vs
  `Sec.`).
- Output formatting of the CLI block and how much paragraph text to quote per line.
- Whether `diff_as_of` returns Pydantic models or plain dicts to the tool layer.
- Where the 10 s budget constant lives (`kanon/config.py` vs module-level in `tools.py`).
- The designator-parsing of `Citation.section`, which is free text from the model (`§ 50.59(a)(2)`,
  `50.59(a)(2)`, `(a)(2)`, `a.2` all seen in the wild) — must fail closed to "no designator
  found" rather than guess a wrong key.

## Not covered

Partial supersession is still document-level: the diff shows paragraph text differences, but the
store does not record *which* paragraphs a `partial` edge replaced. The diff is evidence about
paragraphs; it is not a new authority source, and the gate keeps deciding authority from the graph.
