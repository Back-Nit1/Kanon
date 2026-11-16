# Kanon: results

Final run: 32 pinned questions (19 qa, 13 resolver), question-set sha `c82c87413cc6`, today pinned to 2026-11-14, k = 8, no timeouts. Full tables and per-question rows: `evals/report.md`, `evals/results.json`. Charts: `python3 evals/charts.py`.

Modes: **none** = plain hybrid retrieval, **flag** = filter on today's `status` column, **graph** = bi-temporal authority resolver, which decides the governing set from the supersession graph for the question's date.

## 1. Generation: superseded citations, with answer rate

![generation](evals/charts/generation.svg)

| mode | superseded-citation rate | answer rate |
|---|---|---|
| none | 47% | 100% (19/19) |
| flag | 63% | 100% (19/19) |
| graph | **0%** | 95% (18/19) |

The answer rate is the counter-metric. The one graph abstain is q02 (April 1996), which is expected: no 10 CFR 50.59 version before 1996-08-28 is recorded. Example trace: `evals/examples/abstain_q02_april_1996_anchor.md`.

## 2. Retrieval (no LLM): recall with superseded-chunk rate

![retrieval](evals/charts/retrieval.svg)

| mode | recall@8 | superseded-chunk rate |
|---|---|---|
| none | 0.65 | 78% |
| flag | 0.24 | 74% |
| graph | 1.00 | 0% |

## 3. Resolver, store level (deterministic, no LLM)

![resolver](evals/charts/resolver.svg)

graph 13/13, flag 1/13. The two conflict-window questions (RG 1.85, RG 1.182) are correct when the resolver returns *unresolved*.

## Named case where the status flag fails and the graph succeeds

The spec's case (effective date *before* issue date) does not exist in the corpus. The nearest real case is **effective after issue**: the 1999 rewrite of 10 CFR 50.59 was published 1999-10-04 but took effect 2001-03-13. Question **q06**, asked as of April 2001: `flag` cites the 2025 text (`10CFR50.59@2025-01-29`); `graph` cites the 2001-03-13 revision. Same pattern in q04, q05, q11, q12 and q13. Cross-family supersession (RG 1.1 -> RG 1.82, etc.) is shown at the store level only, because the corpus has no guide text.

## Read this before quoting the numbers

- **Small and narrow.** 14 discriminating questions, all about one regulation and one mechanism (point-in-time lookup). This is a demonstration, not a statistically meaningful benchmark.
- **Graph recall of 1.00 is close to trivial.** The date filter leaves one 50.59 version.
- **Flag is worse than none by construction.** It only knows what is active today, so every past-dated question gets the wrong version. It is a weak stand-in for a flag-based RAG system that could read withdrawal-notice prose.
- **The resolver block is not independent.** Expected sets were derived from the same evidence the store was built from, so graph's 13/13 shows the parser and resolver encode that evidence. Flag's column is the informative one.
- **Run-to-run variance exists.** Between two full runs, flag's superseded-citation rate went 68% -> 63% and none's expected-document citation rate 63% -> 58%. The model is not deterministic and each rate comes from one run of 19 questions.
- **One post-hoc change.** After the first run, graph abstained on q23, a precedent question, because the classifier forced a governing-requirement citation. I narrowed that rule (`kanon/agent.py`, `_needs_requirements`). On the pinned set only q23 changes, but the change was made after seeing the result.
- **Not covered in this corpus:** partial supersession; effective-before-issue; guide text for reg guides (metadata only); four FR rows with no `effective_from`.
- **Not run:** reranker, Ragas, FermiBench, cross-family judge. Tier C judge is advisory and off.
- **Provenance:** four FitzPatrick inspection reports are Internet Archive captures (nrc.gov returned 403); title, date and page count match the ADAMS API.
