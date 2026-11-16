# Kanon: results

Final run: 2026-11-16, 32 pinned questions (19 qa, 13 resolver), question-set sha `c82c87413cc6`,
today pinned to 2026-11-14, k = 8. Full tables and per-question rows: `evals/report.md`,
`evals/results.json`. Charts: `python3 evals/charts.py`.

Modes: **none** = plain hybrid retrieval, **flag** = filter on today's `status` column, **graph** =
bi-temporal authority resolver, which decides the governing set from the supersession graph for
the question's date.

## 1. Generation: superseded citations, with answer rate

![generation](evals/charts/generation.svg)

| mode | superseded-citation rate | answer rate | cites an expected doc |
|---|---|---|---|
| none | 29% | 89% (17/19) | 76% |
| flag | 63% | 100% (19/19) | 26% |
| graph | **0%** | 74% (14/19, 2 of the 5 non-answers are API failures, not abstentions) | 100% |

The answer rate is the counter-metric and this run makes the point for it: `graph`'s rate fell
because the gate got stricter, not because retrieval got worse. Of graph's five non-answers, two
(**q17**, **q23**) are **API failures** — the OpenAI account ran out of credits part-way through
the graph pass, and a failure is not an abstention. Over the 17 graph questions that completed:
14 answered, 3 abstained, 0 superseded citations. **Those two questions need a re-run before this
table is quoted as final.**

The three real abstentions are q02, q03 and q04, and their causes are different:

- **q02** (April 1996, the anchor) is the expected abstention: no 10 CFR 50.59 version before
  1996-08-28 is recorded. Example trace: `evals/examples/abstain_q02_april_1996_anchor.md`.
- **q03 and q04** are new, and they are the price of the paragraph-designator gate rule
  (`e5af5b8`, `d533347`): the model cited a paragraph that does not exist in the revision it
  cited, and `paragraph_not_in_revision` blocks it. That rule is **not** an authority rule, so it
  blocks in every mode, including the baseline — a citation to a paragraph absent from the cited
  text is a hallucinated citation whatever the retrieval mode.

## 2. What the paragraph-designator change did to the previous numbers

The previous report's generation block was measured before `DRAFT_PROMPT` and `Citation.section`
began asking the model for the paragraph designator it cited ((a)(2), (c)(2)(i)) instead of a
section title. That gap is now closed: this is the same pinned question set re-run through the
current prompt and the current gate.

| mode | before | after | what moved |
|---|---|---|---|
| none | 47% @ 100% | 29% @ 89% | q03, q04 now abstain on `paragraph_not_in_revision` (q04 was a superseded citation before); q09, q12, q17 cited a governing revision this time where they cited a superseded one before |
| flag | 63% @ 100% | 63% @ 100% | nothing: identical decisions, identical citations, row by row |
| graph | 0% @ 95% | 0% @ 74% | q03, q04 now abstain on the same rule; q17, q23 failed on exhausted API credits |

Read the baseline's 47% -> 29% with care: **about half of that improvement is the new rule
protecting the baseline**, not better retrieval. Mode `none` has no authority filtering, but the
paragraph check still fires there, so two questions that previously produced confident answers
(one of them a superseded citation) now abstain. The rest is run-to-run variance in which
revision the model chose to cite — the same variance §5 warns about. What did not move is the
headline comparison: `graph` is still the only mode with no superseded citation, and it is still
the only mode that cites an expected document every time it answers.

## 3. Retrieval (no LLM): recall with superseded-chunk rate

![retrieval](evals/charts/retrieval.svg)

| mode | recall@8 | MRR | superseded-chunk rate |
|---|---|---|---|
| none | 0.65 | 0.36 | 78% |
| flag | 0.24 | 0.24 | 74% |
| graph | 1.00 | 1.00 | 0% |

Identical to the previous run, row by row — which is the regression evidence for this session's
other change: the Postgres ingest queue was replaced by a single-process `kanon ingest`, and
re-ingesting the whole corpus through the new path produced the same 3550 chunks (hot 3512, cold
38) and the same retrieval numbers.

## 4. Resolver, store level (deterministic, no LLM)

![resolver](evals/charts/resolver.svg)

graph 13/13, flag 1/13 — unchanged. The two conflict-window questions (RG 1.85, RG 1.182) are
correct when the resolver returns *unresolved*.

## Named case where the status flag fails and the graph succeeds

The spec's case (effective date *before* issue date) does not exist in the corpus. The nearest
real case is **effective after issue**: the 1999 rewrite of 10 CFR 50.59 was published 1999-10-04
but took effect 2001-03-13. Question **q06**, asked as of April 2001: `flag` cites the 2025 text
(`10CFR50.59@2025-01-29`); `graph` cites the 2001-03-13 revision. Same pattern in q05, q11, q12
and q13. Cross-family supersession (RG 1.1 -> RG 1.82, etc.) is shown at the store level only,
because the corpus has no guide text.

## Read this before quoting the numbers

- **Two graph questions did not run.** q17 and q23 failed on exhausted API credits. Graph's answer
  rate is over 19 questions including those two; over the 17 that completed it is 82%.
- **The reranker A/B is still unmeasured.** The cross-encoder (`BAAI/bge-reranker-v2-m3`) loads and
  scores — 41 s to load the weights, 3.9 s for 100 query/chunk pairs on CPU, which is one search's
  worth of candidates — so KANON.md's hypothesis (better topical ranking *raises* the
  superseded-citation rate, because a superseded revision is topically near-identical to the
  current one) is cheap to test. It needs embedding and chat calls, so it is blocked on the same
  billing wall: `uv run kanon-eval --modes none --rerank --out evals/rerank`.
- **Small and narrow.** 14 discriminating questions, all about one regulation and one mechanism
  (point-in-time lookup). This is a demonstration, not a statistically meaningful benchmark.
- **Graph recall of 1.00 is close to trivial.** The date filter leaves one 50.59 version.
- **Flag is worse than none by construction.** It only knows what is active today, so every
  past-dated question gets the wrong version. It is a weak stand-in for a flag-based RAG system
  that could read withdrawal-notice prose.
- **The resolver block is not independent.** Expected sets were derived from the same evidence the
  store was built from, so graph's 13/13 shows the parser and resolver encode that evidence.
  Flag's column is the informative one.
- **Run-to-run variance exists.** Across three full runs, flag's superseded-citation rate went
  68% -> 63% -> 63%, and none's moved 47% -> 29% with three questions changing which revision they
  cited. The model is not deterministic and each rate comes from one run of 19 questions.
- **One post-hoc change.** After the first run, graph abstained on q23, a precedent question,
  because the classifier forced a governing-requirement citation. I narrowed that rule
  (`kanon/agent.py`, `_needs_requirements`). On the pinned set only q23 changed, but the change
  was made after seeing the result.
- **Not covered in this corpus:** partial supersession; effective-before-issue; guide text for reg
  guides (metadata only); four FR rows with no `effective_from`.
- **Not run:** reranker A/B, Ragas, FermiBench, cross-family judge. Tier C judge is advisory and
  off by default (`kanon-eval --judge` turns it on).
- **Provenance:** four FitzPatrick inspection reports are Internet Archive captures (nrc.gov
  returned 403); title, date and page count match the ADAMS API.

## Earlier store fixes (2026-11-15), kept for the record

An audit found three defects in the authority layer, all fixed before this run: `8c0fedc`
(`governs()` ignored the `status` column, so ten withdrawn reg-guide rows with no dated ending
edge resolved as governing), `2d504b0` (unresolved edges recorded with `to_doc=UNKNOWN` were
invisible to the gate), and `421a53a` (`resolve_supersession` ignored its `revision` argument).
None of them moves a number in the tables above, and that was measured rather than assumed.
