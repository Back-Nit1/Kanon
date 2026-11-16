# Kanon evaluation report

- Question set: `/Users/nityanthmaramreddy/gord-demo/evals/questions.yaml` (sha256 `c82c87413cc6`), 32 questions (19 qa, 13 resolver), pinned today = 2026-11-14, k = 8
- Categories: anchor (1), conflict_window (2), control (6), cross_family (6), effective_after_issue (7), time_anchored (10)

## Primary metric (generation): superseded-citation rate, paired with answer rate

| mode | superseded-citation rate | answer rate | answered | cites an expected doc (of answered) |
|---|---|---|---|---|
| none | 29% | 89% | 17/19 | 76% |
| flag | 63% | 100% | 19/19 | 26% |
| graph | 0% | 74% | 14/19 | 100% |

- `none`: superseded-citation rate 29% @ answer rate 89% (17/19 answered)
- `flag`: superseded-citation rate 63% @ answer rate 100% (19/19 answered)
- `graph`: superseded-citation rate 0% @ answer rate 74% (14/19 answered, 2 FAILED)

**Some questions timed out or failed.** Neither is an abstention; those rows have no answer and no gate decision, and are excluded from named failure cases. A failed row means the provider or the stack errored, not that the system refused.

## Retrieval (no LLM)

| mode | recall@k | MRR | superseded-chunk rate | retrieved chunks |
|---|---|---|---|---|
| none | 0.65 | 0.36 | 78% | 152 |
| flag | 0.24 | 0.24 | 74% | 95 |
| graph | 1.00 | 1.00 | 0% | 117 |

Recall@k and MRR are over the questions with a non-empty expected set; superseded-chunk rate is pooled over every retrieved chunk.

## Resolver (store level, deterministic, no LLM) — separate from the two blocks above

- graph (bi-temporal resolver): 13/13 correct
- flag (status column today): 1/13 correct
- `none` has no authority information at all, so it has no resolver row.
- Caveat: these are reg guides with metadata but no text. The flag stand-in is the status column, which a flag RAG system could beat by reading a withdrawal notice's prose. Read this block as 'what each authority model knows', not as RAG accuracy.
- Not independent: expected sets were written from the evidence strings and then checked against this same store, so graph agreement confirms the parser and resolver encode the evidence. The informative column is flag's.

| id | category | doc @ as_of | expected | graph | flag (today's status) |
|---|---|---|---|---|---|
| r01 | control | RG-1.187 @ 2026-11-14 | ['RG-1.187@3'] | ['RG-1.187@3'] ✓ | ['RG-1.187@3'] ✓ |
| r02 | time_anchored | RG-1.187 @ 2019-12-01 | ['RG-1.187@1'] | ['RG-1.187@1'] ✓ | ['RG-1.187@3'] ✗ |
| r03 | effective_after_issue | RG-1.187 @ 2020-06-15 | ['RG-1.187@1'] | ['RG-1.187@1'] ✓ | ['RG-1.187@3'] ✗ |
| r04 | time_anchored | RG-1.187 @ 2020-08-01 | ['RG-1.187@2'] | ['RG-1.187@2'] ✓ | ['RG-1.187@3'] ✗ |
| r05 | cross_family | RG-1.1 @ 2016-01-01 | ['RG-1.82@4'] | ['RG-1.82@4'] ✓ | — ✗ |
| r06 | cross_family | RG-1.1 @ 2026-11-14 | ['RG-1.82@5'] | ['RG-1.82@5'] ✓ | — ✗ |
| r07 | time_anchored | RG-1.3 @ 2016-06-01 | ['RG-1.3@2'] | ['RG-1.3@2'] ✓ | — ✗ |
| r08 | cross_family | RG-1.3 @ 2017-01-01 | ['RG-1.183@0', 'RG-1.195@0'] | ['RG-1.183@0', 'RG-1.195@0'] ✓ | — ✗ |
| r09 | cross_family | RG-1.95 @ 2003-01-01 | ['RG-1.78@1'] | ['RG-1.78@1'] ✓ | — ✗ |
| r10 | conflict_window | RG-1.85 @ 2003-09-01 | unresolved -> abstain | unresolved ✓ | — ✗ |
| r11 | cross_family | RG-1.85 @ 2004-07-01 | ['RG-1.84@32'] | ['RG-1.84@32'] ✓ | — ✗ |
| r12 | conflict_window | RG-1.182 @ 2012-08-01 | unresolved -> abstain | unresolved ✓ | — ✗ |
| r13 | cross_family | RG-1.182 @ 2013-01-01 | ['RG-1.160@3'] | ['RG-1.160@3'] ✓ | — ✗ |

## Named cases: flag fails, graph succeeds

- partial supersession: **not tested** — no question in this category (see coverage notes).
- cross-family supersession: **r05** (resolver) — flag: status column says nothing active; graph: ['RG-1.82@4']
- cross-family supersession: **r06** (resolver) — flag: status column says nothing active; graph: ['RG-1.82@5']
- cross-family supersession: **r08** (resolver) — flag: status column says nothing active; graph: ['RG-1.183@0', 'RG-1.195@0']
- cross-family supersession: **r09** (resolver) — flag: status column says nothing active; graph: ['RG-1.78@1']
- cross-family supersession: **r11** (resolver) — flag: status column says nothing active; graph: ['RG-1.84@32']
- cross-family supersession: **r13** (resolver) — flag: status column says nothing active; graph: ['RG-1.160@3']
- effective date precedes issue date: **not tested** — no question in this category (see coverage notes).
- effective date after issue date (not the spec's named case): **r03** (resolver) — flag: status column says ['RG-1.187@3']; graph: ['RG-1.187@1']
- effective date after issue date (not the spec's named case): **q04** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q05** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q06** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q11** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q12** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q13** (retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- effective date after issue date (not the spec's named case): **q05** (generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@1996-08-28 p.2', '10CFR50.59@1996-08-28 p.2']
- effective date after issue date (not the spec's named case): **q06** (generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2001-03-13 p.1', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2', '10CFR50.59@2001-03-13 p.2']
- effective date after issue date (not the spec's named case): **q11** (generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2007-09-27 p.1']
- effective date after issue date (not the spec's named case): **q12** (generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2024-08-15 p.1']
- effective date after issue date (not the spec's named case): **q13** (generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2024-08-15 p.1']

Other categories (`flag` only knows what is active today, so any past as_of can trip it; these are real but are not the three thesis cases):
- r02 (time_anchored, resolver) — flag: status column says ['RG-1.187@3']; graph: ['RG-1.187@1']
- r04 (time_anchored, resolver) — flag: status column says ['RG-1.187@3']; graph: ['RG-1.187@2']
- r07 (time_anchored, resolver) — flag: status column says nothing active; graph: ['RG-1.3@2']
- r10 (conflict_window, resolver) — flag: status column says nothing active; graph: unresolved (abstains)
- r12 (conflict_window, resolver) — flag: status column says nothing active; graph: unresolved (abstains)
- q03 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q14 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q07 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q08 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q09 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q24 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q10 (time_anchored, retrieval) — flag: recall 0.00, 5 non-governing chunks; graph: recall 1.00, 0 non-governing chunks
- q02 (anchor, generation) — flag: answer, cited ['EA-98-155@0 p.1']; graph: abstain, cited nothing
- q14 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@1996-08-28 p.2']
- q07 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2001-03-13 p.1']
- q08 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2001-03-13 p.1']
- q09 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2001-12-14 p.1']
- q24 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2001-12-14 p.1']
- q10 (time_anchored, generation) — flag: answer, cited ['10CFR50.59@2025-01-29 p.1']; graph: answer, cited ['10CFR50.59@2007-09-27 p.1']

## Coverage notes (from the pinned question file)

- Effective date AFTER issue date: real and tested. (1) The 1999 rewrite of 10 CFR 50.59 was published 1999-10-04 but took effect 2001-03-13, a date set by a different document (M8, E2, E4, E6): q04, q05, q06. (2) The 2024 and 2025 amendments each took effect 30 days after publication (M12, M13): q11, q12, q13. (3) RG 1.187 Rev 2 is dated 06/2020 in the index, but 'takes effect on July 7, 2020' (E33): resolver r03 (metadata only, no guide text).
- Cross-family supersession: tested at the STORE level only (resolver questions r05, r06, r08, r09, r11, r13). There is no guide text in the corpus, so it is NOT measurable at the retrieval or generation level. Those flag-baseline answers are the status column; a flag RAG system could still read the right successor from the FR withdrawal notice's prose, so the store-level comparison understates flag. Where the source names no successor revision (E13 'incorporated into RG 1.82'; E17 'RG 1.183 ... and RG 1.195'), the loader pins the revision in effect on the withdrawal date, with confidence 'inferred'.
- Partial supersession: none in the data. Every 50.59 amendment is modelled as a full successor of the compiled text. The one genuine partial case, 61 FR 39278 adding (d) and (e) while (a)-(c) kept governing, can't be modelled because no pre-1996-08-28 version is recorded (EVALUATION 4.2 G). No license-amendment page swaps were obtainable.
- Effective date BEFORE issue date (the case the spec names): no instance exists. ACCESS_REPORT says 'none found', and EVALUATION 4.2 H confirms that no row has effective_from < issue_date.
- Conflict window, a fourth real pattern: the index dates the incorporation into the successor earlier than the formal withdrawal (RG 1.85: 06/2003 vs 2004-06-18; RG 1.182: 05/2012 vs 2012-11-27). Inside the window the resolver reports the authority as UNRESOLVED and the correct output is abstention (r10, r12).
- Anchor (EA-98-155, April 1996): the earliest recorded 50.59 version (M1) took effect 1996-08-28, and the 1996 CFR edition is not on GovInfo, so q02 expects abstention. M1's text for (a)-(c) did govern in April 1996, but recording that needs a pre-1996 version row.
- FitzPatrick inspection report ML22027A362 is in the manifest (Internet Archive capture; nrc.gov returned 403, see its notes field). The procedure it names, MP-033.05 Rev 28, is not public and is not in the corpus (ACCESS_REPORT rows A, H).
- FR PDFs carry first- and last-page bleed from other agencies' documents. No answer here depends on those pages: the 50.59 instructions cited are on interior pages, as given in each manifest row's notes.
- Discriminating QA questions: 14 (q02-q14, q24), all about one regulation and one mechanism (point-in-time lookup). That is enough for a demonstration, not for statistically meaningful retrieval metrics (EVALUATION 4.3). In mode graph the pre-filter leaves one 50.59 version, so graph recall is close to trivially high.

## Notes

- 'Superseded' is decided by `AuthorityStore.governs` against each question's pinned as_of — never by a model. Records (operating experience, FR rules and notices) are exempt: they record what happened, not what governs.
- Reranker run (`--rerank`, cross-encoder over the top ~100 pre-filtered candidates): not run in this pass; off unless KANON_RERANK_MODEL is set.
- Tier C judge annotations, when enabled, are advisory and live in the traces; they do not feed any metric here. Judge shares a provider with the RAG model by default.
