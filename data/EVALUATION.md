# Kanon data: independent evaluation

Evaluated 2026-11-14 against `data/manifest.jsonl` (22 rows), `data/labels/supersession_evidence.jsonl` (34 rows) and `data/labels/rg_index.jsonl` (121 rows), all with mtime 03:17:51. I only read the data. The only files I wrote are this report and `data/eval_scripts/`. Check scripts are in `data/eval_scripts/` and can be re-run with `python3 -I data/eval_scripts/<script>.py data`:

- `check_manifest.py`: fields, enums, dates, sha256, page counts, text layer.
- `check_evidence.py`: verbatim quotes and pages.
- `check_timeline.py`: 50.59 intervals.

The scratch database is `data/eval_scripts/scratch_authority.db`. Network use was limited to govinfo.gov and federalregister.gov, for re-checking facts. Nothing was fetched from nrc.gov.

## Verdict: usable with caveats, and only for one of the thesis's three claims

What's on disk is **accurate**:

- Every file matches its sha256.
- Every PDF page has a real text layer.
- 33 of 34 evidence quotes match the source text verbatim after normalization. The 34th is correct but only machine-checkable with `pdftotext -layout`.
- The 10 CFR 50.59 point-in-time chain is correct, contiguous and independently confirmed. The FR effective dates, the CFR citation lines in the 1997, 1998, 1999, 2000, 2001 and 2002 editions plus eCFR, and the eCFR text diffs all agree.

The corpus is **narrow**: in effect one regulation family (6 versions of 50.59), plus 7 FR rules, 8 FR notices about guides, and one transcribed enforcement letter.

What it can and can't support:

- **Effective date after issue date: supported.** The 1999 rule was published 1999-10-04 and took effect 2001-03-13, and the 2024 and 2025 amendments each took effect 30 days after publication. This is a real, well-evidenced case where `flag` fails and `graph` succeeds.
- **Partial supersession: cannot be measured.**
- **Cross-family supersession: cannot be measured.** No regulatory guide text is on disk. All 14 guides named in the evidence are missing as documents, and RG 1.78 is missing even from the index metadata. A `flag` system can answer every cross-family question correctly just by reading the FR withdrawal notice.
- **Effective date before issue date (the case the spec names): no instance exists.**
- **Anchor date: not covered.** The anchor's as-of date (April 1996) falls before the earliest recorded 50.59 version (1996-08-28), so `graph` returns nothing for it, even though the governing text, paragraphs (a)–(c), is on disk.
- **Evals:** about 14 questions honestly separate `flag` from `graph`. All are about one regulation and all use one mechanism (point-in-time lookup). That is enough for a demonstration, not for statistically meaningful retrieval metrics.

**Counts: 23 PASS, 8 FAIL, 25 WARN** (itemized in the Tally at the end).

---

## 1. Manifest integrity

| # | Result | Item | Evidence |
|---|---|---|---|
| 1.1 | **PASS** | Fields, enums and ISO dates | All 22 rows have exactly the 19 contract fields. `kind` and `status` values are in the enums from `kanon/store.py` SCHEMA. Every date is `YYYY-MM-DD`. No duplicate `(doc_id, revision)`. No `effective_to <= effective_from`. Every `superseded` row has an `effective_to`, and no `active` row does. (`check_manifest.py`) |
| 1.2 | **PASS** | `local_path` exists | 22/22 |
| 1.3 | **PASS** | sha256 matches disk | 22/22 |
| 1.4 | **PASS** | `page_count` equals `pdfinfo` | 16/16 PDFs (2,2,2,27,36,2,2,215,5,20,3,2,2,2,2,1,1,1 pp; 327 pp total) |
| 1.5 | **PASS** | `paginated` flags | All PDFs are `true`. The 3 eCFR XML files and the EA-98-155 `.txt` are `false` with `page_count: null`. |
| 1.6 | **PASS** | Text layer (not scans) | Every one of the 327 PDF pages yields at least 200 characters from `pdftotext`. |
| 1.7 | WARN | EA-98-155 is not original bytes | `raw/nrc_html/ea98155.extract.txt` line 1: "DOM text extraction (NOT original bytes)". Its sha256 is of the transcription. `verified: true` refers to the identity check made in a browser. I could not re-verify the content (nrc.gov returns 403). Hard constraint 1 ("Real data only") is met in spirit only. |
| 1.8 | WARN | 4 rows have `effective_from: null`: FR-65-77773, FR-77-70846, FR-69-34202, FR-67-939 | `store.governs()` returns False when `effective_from` is None, so in `graph` mode these four never govern. In `flag` mode they are `active` and do. Scoring is therefore asymmetric for a data reason, not an authority reason. For the three notices the FR publication date is in the PDF header (e.g. "Tuesday, November 27, 2012") and could be used as an inferred date. |
| 1.9 | WARN | Every FR rule row is `status: active` with `effective_to: null`, permanently | FR-64-53582 contains the full 1999 text of § 50.59 (PDF p.32, "§ 50.59 Changes, tests, and experiments."), including "(i) A change to the technical specifications". The 2024 rule replaced that wording. Both `flag` and `graph` will treat that 1999 text as governing today, so citing it will **not** be counted as superseded in any mode. The same applies to the RG 1.187 Rev 1 and Rev 2 notices (FR-84-25077, FR-85-40696), which "govern" alongside Rev 3; the note on q18 in `evals/questions.yaml` already admits this. |
| 1.10 | WARN | The six 10CFR50.59 rows have `issue_date: null` | The effective-versus-issue gap is recorded only on the FR rows. A recency or issue-date baseline over the 50.59 rows has nothing to sort on. The 1-1-2000 CFR edition, which prints the 1999 text before it took effect, is on disk but not in the manifest. It is the natural "issued 2000-01-01, effective 2001-03-13" row. |
| 1.11 | WARN | Unrelated documents inside FR PDFs | 11 of 15 FR PDFs contain other documents' FR Doc numbers. Examples: `FR-1996-07-29` includes a poultry bibliography and an SBA correction ("EFFECTIVE DATE: July 29, 1996.", line 5962). `FR-2000-12-13` includes an airworthiness directive ("This AD becomes effective on"). `FR-2015-04-30` includes a Vermont Yankee exemption table (ML14080A141 …). Their chunks will carry the NRC `doc_id`, which pollutes retrieval and gives false "effective date" hits. |
| 1.12 | WARN | Corpus balance | FR-72-49352 is 215 of the 327 PDF pages (66%), and only PDF p.149 concerns 50.59. Chunk-level retrieval will be dominated by Part 52 text. |

## 2. Fact verification

| # | Result | Item | Evidence |
|---|---|---|---|
| 2.1 | **PASS** | `issue_date` of all 15 FR rows | Every PDF's running header matches. For example, FR-64-53582: "Federal Register / Vol. 64, No. 191 / Monday, October 4, 1999". The start page equals the `doc_id` number (e.g. the 01-30832 text begins "64737"). |
| 2.2 | **PASS** | Effective dates quoted in `date_basis` | 61 FR 39278 l.94 "EFFECTIVE DATE: August 28, 1996."; 66 FR 64737 l.39 "EFFECTIVE DATE: December 14, 2001."; 72 FR 49352 l.40 "The effective date is September 27, 2007"; 89 FR 57717 l.43 and 89 FR 106234 l.294 "This final rule is effective on …"; 80 FR 24293 "Effective April 30, 2015, the NRC withdraws RG 1.1."; 81 FR 88710 "…is December 8, 2016."; 84 FR 25077 "Revision 1 to RG 1.187 is available on May 30, 2019."; 85 FR 40696 "takes effect on July 7, 2020"; 86 FR 31346 "is available on June 11, 2021." |
| 2.3 | **PASS** | 1999 rule deferral, re-derived | 64 FR 53582 p.1: "Sections 50.59, 50.66, 50.71(e), and 50.90 become effective 90 days after issuance of applicable regulatory guidance." Part 72 sections are effective February 1, 2000, and 72.212/72.48 on April 5, 2001. 65 FR 77773 DATES: "The effective date of the October 9, 1999 amendment to 10 CFR 50.59 (64 FR 53613) is March 13, 2001." The "October 9" is sic; the body (l.266) says "October 4, 1999". It was signed "this 6th day of December 2000". |
| 2.4 | **PASS** | federalregister.gov metadata traps exist | The live API returns `effective_on: 2000-02-01` for 99-25054 and `1999-10-09` for 00-31735. Both are wrong for 50.59, exactly as the manifest notes say. |
| 2.5 | **PASS** | 50.59 intervals contiguous, no overlap | `check_timeline.py`: [1996-08-28, 2001-03-13) → [2001-03-13, 2001-12-14) → [2001-12-14, 2007-09-27) → [2007-09-27, 2024-08-15) → [2024-08-15, 2025-01-29) → [2025-01-29, open). The version boundaries equal the 6 FR effective dates exactly. |
| 2.6 | **PASS** | No missing amendments | Citation lines: the 1-1-97 edition ends "57 FR 39358, Aug. 31, 1992; 61 FR 39300, July 29, 1996". I fetched the 1-1-98 and 1-1-99 editions from govinfo and they have the identical citation line, so the old text was not amended between 1997 and 1999. 1-1-02: "[64 FR 53613, Oct. 4, 1999, as amended at 66 FR 64738, Dec. 14, 2001]". The eCFR 2017 snapshot ends "72 FR 49500, Aug. 28, 2007", so nothing changed between 2007 and 2016. The 2024-08-15 and 2025-01-29 snapshots add 89 FR 57720 and 89 FR 106251. |
| 2.7 | **PASS** | Text changes occur at the recorded boundaries | (b) 2001 edition: "possession but not operation"; 2002 edition: "possession of nuclear fuel, but not operation" (66 FR 64737). eCFR diffs: 2017→2024-08-15 changes only "(i) A change" to "(i) An amendment"; 2024-08-15→2025-01-29 changes only (b) (adds "or a non-power production or utilization facility that has permanently ceased operations"). The 1-1-97 (d) and (e) text matches the 61 FR 39278 instruction 21. |
| 2.8 | **PASS** | FR page citations in notes | 64 FR 53613 is PDF p.32. 72 FR 49500 is p.149 ("■ 92. In § 50.59, paragraphs (b), (d)(2),"). 89 FR 106251 is p.18. 89 FR 57720 is p.4. 66 FR 64738 is p.2. |
| 2.9 | **FAIL** | No 50.59 version covers the anchor date (April 1996) | Row 1 has `effective_from: 1996-08-28`, but its own notes say "Paragraphs (a)-(c) are the text in force during the April 1996 EA-98-155 pump change". The citation line shows that the last amendment before 1996 was 57 FR 39358 (1992), and 61 FR 39278 only *added* (d) and (e). So the governing text for 1996-04-01 is on disk but not recorded as a version. Result: `kanon resolve 10CFR50.59 --as-of 1996-04-01` prints `NOTHING` (section 5), and q02 is forced to expect abstention on the spec's own anchor scenario. |
| 2.10 | WARN | RG 1.84 Rev 32 date conflict | The index says Rev 32 was issued 06/2003, and E21 says "06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of RG 1.84". But FR-69-34202, published 2004-06-18 and signed "this 10th day of June, 2004", says "NRC has issued a revision of a guide … With the issuance of Revision 32 to Regulatory Guide 1.84, Regulatory Guide 1.85 … is being withdrawn". Rev 32 therefore has two candidate dates a year apart. The loader picks 2003-06-01. This cannot be resolved without the RG 1.84 Rev 32 PDF. |
| 2.11 | **PASS** | RG 1.187 Rev 2 effective ≠ issue | The index says 06/2020. FR-85-40696 says "Revision 2 to RG 1.187 takes effect on July 7, 2020." The loader correctly overrides to 2020-07-07 (`resolve RG-1.187 --as-of 2020-06-15` gives Rev 1). |
| 2.12 | WARN | EA-98-155 content not independently verifiable | It is internally consistent: "May 13, 1998", "EA 98-155", "Docket No. 50-002 / License No. R-28", "Dated this 13th day of May 1998", and the 50.59 paraphrase "does not involve a change in the technical specifications" matches the pre-1999 (a)(1) text in the 1-1-97 edition. I could not compare it to the original (403). |
| 2.13 | WARN | FitzPatrick accessions (ML22027A362, ML15219A611, ML12166A406, ML083150382) | Not on disk. The identity claims rest only on the gatherer's browser reading. The "19 / 20 separate 50.59 screenings" counts in KANON.md remain unverified (ACCESS_REPORT row A says so too). |
| 2.14 | WARN | EA-98-155 kind and dating | `effective_from` is set to the letter date. That is fine for an event record. Note that the event itself (April 1996) predates the record (1998-05-13). A question "as of 1996, has anything gone wrong before?" should correctly *not* surface EA-98-155. |

## 3. Evidence labels (`supersession_evidence.jsonl`)

| # | Result | Item | Evidence |
|---|---|---|---|
| 3.1 | **PASS** | Verbatim quotes | 33 of 34 match after NFKC, line-end de-hyphenation, quote normalization and whitespace collapse. All 23 PDF-sourced rows with `source_page` are on the stated 1-based page, except row 9 (see 3.2). (`check_evidence.py`) |
| 3.2 | WARN | Row 9 (FR-89-57717, "9. In § 50.59, amend paragraph (c)(1)(i) …") fails a plain `pdftotext` check | Column interleaving breaks the plain extraction. `pdftotext -layout -f 4 -l 4` shows it intact: "■ 9. In § 50.59, amend paragraph / (c)(1)(i) by removing the text ''A / change'' and adding in its place the text / ''An amendment''." The quote is correct apart from the omitted "■", and it is already flagged MANUAL in its notes. Any automated re-check must special-case it. |
| 3.3 | **PASS** | `stated_date` values that are given | All 17 non-null values match the source: the 6 FR effective dates, 2015-04-30 ×2, 2016-12-08 ×3, 2004-06-18, 2002-01-08, 2012-11-27, 2000-12-13 (publication), 2019-05-30, 2020-07-07, 2021-06-11. |
| 3.4 | **FAIL** | `stated_date: null` where the source document states a date | **Row 13** (RG 1.1 → RG 1.82): the same FR document says "DATES: Effective April 30, 2015, the NRC withdraws RG 1.1." (row 12). Because the date is null, the edge loads as `unresolved` and blocks RG 1.1 at *every* as-of date (section 5). **Rows 23, 26, 29, 30** (FR notices for RG 1.85, 1.95 and 1.182) have no effective-date sentence, but the FR publication date is in the PDF header and could be used as an inferred date. All four load as `unresolved`. After the final run, `store.unresolved_into`'s corroboration rule hides rows 23, 26, 29 and 30, because each has a dated twin (E21, E24, E27). Row 13 has no dated twin, so it still blocks: `resolve RG-1.1` abstains at every date. |
| 3.5 | WARN | Undated index HISTORY items duplicate dated FR statements (handled in code) | Rows 14–16 ("Guidance from RG 1.3 has been updated and incorporated into RG 1.183 … and RG 1.195 …") carry no date in the source, so they load as 6 `unresolved` edges. These sit alongside 6 resolved edges from rows 17–19 (FR, 2016-12-08). `store.unresolved_into` (corroboration rule) now suppresses them: `resolve RG-1.3 --as-of 2020-01-01` gives `[('RG-1.183','0'), ('RG-1.195','0')]` with no UNRESOLVED line. The data is still untidy, and the same rule also hides a real date *conflict* (see L2). |
| 3.6 | WARN | Index-derived quotes come from browser transcriptions | Rows 11, 14–16, 20–22, 24–25 and 27–28 can only be checked against `raw/nrc_html/*.extract.txt` ("NOT original bytes"), not the original HTML. Odd spacing such as row 22 "Materials code case Acceptability - - ASME Section III, Division 1(Withdrawn See -- …" is plausibly verbatim but unverifiable. |
| 3.7 | WARN | Non-supersession rows in the supersession file | Row 20 (a DG-1090 draft history item) and rows 31–34 (RG 1.187 issuance statements) produce no edges. They are harmless, but they inflate "34 evidence rows". Only 22 rows carry an actual supersession or withdrawal statement. |

## 4. Fitness for purpose

### 4.1 Anchor question (pump replacement, EA-98-155, as of 1996)

| Part of the answer | On disk? | Status |
|---|---|---|
| Which 50.59 text governed in April 1996 | Text yes: the 1-1-97 edition (a)–(c), "...without prior Commission approval, unless the proposed change, test or experiment involves a change in the technical specifications incorporated in the license or an unreviewed safety question." Version no: no row covers 1996-04-01. | **FAIL** (see 2.9) |
| Did it need prior NRC approval | Only the rule test is on disk. The facility facts (Ford Nuclear Reactor TS 2.1.1 and 2.2.1, SAR §4.1 flow 900–1000 gpm, TS Amendment 44) are quoted only inside the EA-98-155 transcription. No TS, SAR, IR 50-002/98202 or amendment is on disk. | WARN |
| Has anything gone wrong before | EA-98-155 is on disk (transcription). As of 1996 the correct answer is "no recorded precedent", because the EA is dated 1998. The FitzPatrick IRs (pump seal OE, 50.59 screenings) are missing. | WARN |
| Same question as of today | 10CFR50.59@2025-01-29 (eCFR XML) plus EA-98-155 answer the regulatory and precedent parts. The guidance layer (RG 1.187 Rev 3, NEI 96-07) is missing: only the FR notices are present. | WARN (answerable, but thin) |

### 4.2 Real cases where `flag` fails and `graph` succeeds

`flag` is a snapshot of *today's* status, so it fails *every* past as-of question by construction. I only count a case as thesis-grade if it is one of the three named mechanisms, both documents (old and new) have text in the corpus, and the evidence is verbatim.

| # | Case | Mechanism | Docs | Verdict |
|---|---|---|---|---|
| A | 50.59 1999 revision, as of any date in [1999-10-04, 2001-03-12] | Effective date **after** issue date, with the effective date set by a different document | Governs: `10CFR50.59@1996-08-28`. Traps: `FR-64-53582@0` (issued 1999-10-04, `active`, contains the new § 50.59 text on PDF p.32) and `10CFR50.59@2025-01-29` (`active`). Also on disk but not in the manifest: the 1-1-2000 CFR edition, which already prints the new text with "EDITORIAL NOTE: See 64 FR 53582, Oct. 4, 1999, for effectiveness of § 50.59." Evidence: E2, E3, E4, E6. | **REAL, STRONG.** `resolve --as-of 2000-06-01` gives `[('10CFR50.59','1996-08-28')]`. `flag` would surface the 2025 text and the 1999 rule. It also defeats issue-date recency ranking, but only if a recency baseline exists, which is not one of the three modes. |
| B | CFR edition "revised as of 1-1-01", as of 2001-01-01 to 2001-03-12 | Same mechanism, compiled-edition trap | `10CFR50.59@2001-03-13` (the 1-1-01 edition prints the new text) vs. governing `10CFR50.59@1996-08-28`. E6: "…was confirmed as Mar. 13, 2001." | **REAL** (q05). Same root cause as A. |
| C | 89 FR 57717, as of 2024-07-16 to 2024-08-14; 89 FR 106234, as of 2024-12-30 to 2025-01-28 | Effective 30 days after issue | `10CFR50.59@2007-09-27` and `@2024-08-15` govern inside those windows. The eCFR versions API lists the publication days as amendment dates. | **REAL, WEAK** (30-day windows, one-word and one-clause changes). Good adversarial questions (q11, q13). |
| D | RG 1.187 Rev 2: index date 06/2020, takes effect 2020-07-07 | Effective after issue date | Metadata only (rg_index row plus FR-85-40696). No RG text. | **WEAK.** One month, metadata only. Cannot show up in retrieval metrics. |
| E | RG 1.1 → RG 1.82 (pump NPSH) | Cross-family | No text for either guide. FR-80-24293 is `active` and states the answer in prose, so `flag` answers correctly from it. | **NOT MEASURABLE.** The loader also leaves the edge `unresolved` (3.4). |
| F | RG 1.3/1.4/1.5 → RG 1.183 + RG 1.195 | Cross-family, one-to-many | No text for any of the five guides. `graph` returns `[RG-1.183@0, RG-1.195@0]` from metadata, and `flag` reads the same answer from FR-81-88710. | **NOT MEASURABLE** at retrieval level. |
| G | Partial supersession | — | Every 50.59 amendment is modelled as a full successor of the compiled text, which is correct. The only genuine partial case is 61 FR 39278 adding (d) and (e) while (a)–(c) keep governing from before. It is not modelled because the pre-1996 version is missing (2.9). No license-amendment page swaps exist. | **NONE.** |
| H | Effective date *before* issue date (the case the spec names) | — | ACCESS_REPORT: "none found". I confirm: no row has `effective_from < issue_date`. | **NONE.** The report must say so (KANON.md: "If no such case exists in the corpus, say so"). |

### 4.3 Retrieval metrics and question count

- **Documents with text and pages:** 22 documents, of which 18 are paginated PDFs (327 pp). Only about 25 pages are about 50.59 or the guides; the rest is Part 52 or neighbouring FR documents. The six 50.59 versions are near-duplicates: consecutive versions differ by one word or one clause after 2001. So recall@k and MRR in mode `none` measure tie-breaking among near-identical chunks, and in mode `graph` they are almost trivially 1.0 because the pre-filter leaves one version. The numbers will be **illustrative, not statistically meaningful** (n of about 14 discriminating questions, one document family). WARN.
- **Honest count of questions that separate `flag` from `graph`:** about 14, two per text-change boundary (before and after) plus the two deferral-window traps:

| Boundary or trap | Question as of | Expected (doc_id@rev) |
|---|---|---|
| (d)/(e) added, 1996-08-28 | 1996-04-01 | `10CFR50.59@<pre-1996>`: **not recordable today**, so the question must expect abstention |
| | 1997-06-01 | `10CFR50.59@1996-08-28` |
| 1999 rewrite, deferral window | 2000-06-01 | `10CFR50.59@1996-08-28` (trap: FR-64-53582) |
| | 2001-01-01 | `10CFR50.59@1996-08-28` (trap: the 1-1-01 edition) |
| | 2001-04-01 | `10CFR50.59@2001-03-13` |
| "of nuclear fuel," 2001-12-14 | 2001-09-01 | `10CFR50.59@2001-03-13` |
| | 2005-01-01 | `10CFR50.59@2001-12-14` |
| Part 52 in (b), 2007-09-27 | 2006-01-01 | `10CFR50.59@2001-12-14` |
| | 2010-06-01 | `10CFR50.59@2007-09-27` |
| (c)(1)(i) "amendment", 2024-08-15 | 2024-08-01 | `10CFR50.59@2007-09-27` |
| | 2024-09-01 | `10CFR50.59@2024-08-15` |
| (b) non-power clause, 2025-01-29 | 2025-01-15 | `10CFR50.59@2024-08-15` |
| | today | `10CFR50.59@2025-01-29` (+ `FR-89-106234@0`) |
| RG 1.187 revisions (FR notice proxies) | 2019-12-01 / 2020-08-01 | `FR-84-25077@0` / `FR-85-40696@0`: weak, because notices stand in for guides |

The existing `evals/questions.yaml` (23 questions) matches this analysis. Its q03–q14 and q19–q20 are the discriminating ones. Its q01, q15–q18 and q21–q23 are "current" or precedent questions where `flag` and `graph` give the same set, so they pad the count to 23 without adding discrimination. Its `coverage_notes` correctly say that partial, cross-family and effective-before-issue are not tested. WARN: reaching 20–25 *discriminating* questions is not possible with this data.

### 4.4 Guides referenced in evidence but missing as documents

None of the guides has text in the corpus. The loader creates metadata-only rows from `rg_index.jsonl` (96 numbered versions). Where an edge points at a guide:

| Guide | In rg_index (metadata)? | Text on disk? | Edge effect |
|---|---|---|---|
| RG 1.1 (NPSH, pumps) | yes (Rev 0) | no | subject of E11–E13 |
| RG 1.82 (Rev 0–5) | yes | no | target of E13 (unresolved, see 3.4) |
| RG 1.3, 1.4, 1.5 | yes | no | subjects of E14–E19 |
| RG 1.183 (Rev 0, 1) | yes | no | target, pinned to Rev 0 |
| RG 1.195 (Rev 0) | yes | no | target, pinned to Rev 0 |
| RG 1.84 (Rev 31–40 only) | partial | no | target Rev 32 |
| RG 1.85 (Rev 30, 31) | yes | no | subject |
| RG 1.95 (Rev 1) | yes | no | subject |
| **RG 1.78** | **no** | no | **FAIL.** The E24 target `RG-1.78@1` does not exist in the store. `resolve RG-1.95 --as-of 2003-01-01` prints `NOTHING`, with no UNRESOLVED line naming the missing node (section 5). |
| RG 1.160 (Rev 0–4) | yes | no | target Rev 3 |
| RG 1.182 | yes | no | subject |
| RG 1.187 (Rev 0–3) | yes | no (FR notices only) | the 50.59 implementation guide, central to the anchor |

## 5. Loader run (scratch DB)

The final run (`KANON_DB=data/eval_scripts/scratch_authority.db`, after rm) used `kanon/supersession.py` (mtime 03:22:03) and `kanon/store.py` (03:23:17). The final run happened at 03:32, after confirming that neither file had changed since 03:23.

```
documents: 22 from manifest + 96 reg guide versions (metadata only)  edges: {'explicit': 5, 'inferred': 80, 'unresolved': 12}
```

Of the 97 edges, 67 are `[derived]` same-document revision-succession edges (`infer_revision_edges`, all `inferred`). 30 are parsed from evidence: 5 explicit, 13 inferred, 12 unresolved.

| Command | Output | Correct? |
|---|---|---|
| `resolve 10CFR50.59 --as-of 1996-04-01` | `Governing on 1996-04-01: NOTHING` (no UNRESOLVED line) | **FAIL** (data). The answer should be the pre-1996 text, (a)–(c), and if it can't be given, the abstention should name the gap. The E1 edge `10CFR50.59@1996-08-28 → 10CFR50.59@*` is stored `unresolved` but has `to_rev='*'`, which is never seeded, so it is invisible to `resolve`. |
| `--as-of 2000-06-01` | `[('10CFR50.59', '1996-08-28')]` | **PASS** (case A) |
| `--as-of 2001-03-13` | `[('10CFR50.59', '2001-03-13')]`, with chain `2001-03-13 --full--> 1996-08-28 (effective 2001-03-13, inferred)` | **PASS** (the boundary day goes to the new version, half-open interval) |
| `--as-of 2026-11-14` | `[('10CFR50.59', '2025-01-29')]`, with 10 chain edges (5 from evidence and 5 derived duplicates) | **PASS.** Duplicates are harmless. |
| `resolve RG-1.1 --as-of 2010-01-01` | `[('RG-1.1', '0')]` plus `UNRESOLVED RG-1.82 Rev * --cross_family--> RG-1.1 Rev 0 (effective unknown date, unresolved)` | **WARN.** The authoritative set is right (RG 1.1 was withdrawn only in 2015), but the gate will abstain because of an unresolved edge whose date is actually stated in the same FR document. |
| (extra) `resolve RG-1.1 --as-of 2026-11-14` | `NOTHING` plus the same UNRESOLVED edge | The abstention names the edge, as designed. With L1 fixed it would resolve to `RG-1.82@5` through `RG-1.82@4`. |
| (extra) `resolve RG-1.3 --as-of 2020-01-01` | `[('RG-1.183','0'), ('RG-1.195','0')]` | PASS (one-to-many, metadata only) |
| (extra) `resolve RG-1.95 --as-of 2003-01-01` | `NOTHING`, with no UNRESOLVED line | FAIL (L3: the target RG-1.78 is not in the store) |

Edges I believe are wrong or mis-typed:

| # | Evidence (verbatim, truncated) | Produced | Should produce |
|---|---|---|---|
| L1 | E13 "The NRC is withdrawing RG 1.1 because its guidance has been incorporated into RG 1.82, ''Water Sources…''" | `RG-1.82@* → RG-1.1@0` cross_family, **unresolved**, no date | cross_family, valid_from **2015-04-30** (from "DATES: Effective April 30, 2015, the NRC withdraws RG 1.1."), from_rev pinned to **RG-1.82@4** (Rev 4 dated 2012-03 was in effect then), `inferred`. Root cause: the row's `stated_date` is null (3.4). |
| L2 | E23 "With the issuance of Revision 32 to Regulatory Guide 1.84, Regulatory Guide 1.85 … is being withdrawn because …incorporated into Revision 32 of Regulatory Guide 1.84." | `RG-1.84@32 → RG-1.85@31`, unresolved. Hidden by the corroboration rule because E21 is a dated twin. | Same edge, valid_from 2004-06-18 (FR publication), `inferred`. This is not an undated copy: it **conflicts** with E21's 2003-06-01 by a year. The corroboration rule treats it as redundant and silently keeps 2003-06-01. Surface it as a date conflict instead (2.10). |
| L3 | E26 "…incorporated into Revision 1 of Regulatory Guide 1.78." | `RG-1.78@1 → RG-1.95@1`, unresolved | valid_from 2002-01-08, `inferred`. Also, the E24 version of this edge (`inferred`, 2002-01-01) points at **RG-1.78@1, which is not in the store**: `resolve RG-1.95 --as-of 2003-01-01` prints `NOTHING` silently. An edge whose target node is missing should be `unresolved` ("target not in store"). |
| L4 | E29 "…redundant due to the inclusion of its subject matter in Rev. 3 of RG 1.160…" and E30 "…already contained in Regulatory Guide 1.160." | `RG-1.160@3 → RG-1.182@0` and `RG-1.160@* → RG-1.182@0`, both unresolved | valid_from 2012-11-27 (FR publication), `inferred`. E30's target should pin to Rev 3, the revision in effect on 2012-11-27. |
| L5 | E14–E16 "Guidance from RG 1.3 [1.4, 1.5] has been updated and incorporated into RG 1.183, … and RG 1.195, …" | 6 unresolved cross_family edges (suppressed at resolve time by `unresolved_into`) | These are corroborating duplicates of E17–E19, which loaded as `inferred` with date 2016-12-08. Either date them from the same guide's "W 12/2016" row, or don't let an undated duplicate block a resolved edge with the same (from, to). The code now does the second option, so this is a WARN on data hygiene only. |
| L6 | E21 "06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of RG 1.84" | `RG-1.84@32 → RG-1.85@31` cross_family, 2003-06-01, inferred | Parsed correctly. But see L2: it ends RG 1.85's authority a year before the formal withdrawal (69 FR 34202, 2004-06-18). Flag it as a conflicting-date edge, not a silent choice. |
| L7 | E9–E10 and E7–E8 (CFR amendment instructions with an explicit `stated_date`) | `full`, **inferred** | Type and date are right. Confidence drops to `inferred` only because the old-side revision is pinned by wildcard. That is acceptable, but it means the strongest real edges in the corpus never show as `explicit`. Only the 5 withdrawal edges do. |

## 6. Gaps: what to acquire next, in priority order

nrc.gov blocks automation, so items 1–4 need the user to download the files by hand into `data/raw/adams/`. Every accession below was read from a file on disk (an FR notice or ACCESS_REPORT). None is guessed.

1. **The pre-1996 50.59 version, which fixes the anchor date.** No nrc.gov access is needed.
   - (a) Data fix now: add a row `10CFR50.59@pre-1996-08-28` covering paragraphs (a)–(c) of `raw/govinfo/CFR-1997-title10-vol1-sec50-59.pdf`, with `effective_to: 1996-08-28`. Back it with the citation line and the 61 FR 39278 "paragraphs (d) and (e) are added" instruction.
   - (b) To date its start honestly, fetch 57 FR 39358 (Aug. 31, 1992) from govinfo. I confirmed that `https://www.govinfo.gov/content/pkg/FR-1992-08-31/pdf/FR-1992-08-31.pdf` returns HTTP 200; it is a 255 MB whole-issue PDF, so extract the page range. Without it, the row's `effective_from` is unknown, and the resolver should abstain while naming that fact rather than print NOTHING.
2. **RG 1.187 Rev 1, 2 and 3** (the 50.59 implementation guidance, which answers "does it need prior approval"). Accessions are taken from the on-disk FR notices:
   - Rev 1: ML17195A655 → `https://www.nrc.gov/docs/ML1719/ML17195A655.pdf`
   - Rev 2: ML20125A730 → `https://www.nrc.gov/docs/ML2012/ML20125A730.pdf`
   - Rev 3: ML21109A002 → `https://www.nrc.gov/docs/ML2110/ML21109A002.pdf`
   - These give a same-family text chain plus the Rev 2 effective-versus-issue case (D) with real text. Rev 0 (Nov 2000) has no verified accession on disk.
3. **FitzPatrick IRs** (pump operating experience and 50.59 screening density; KANON.md's corpus target). Identity was confirmed in ACCESS_REPORT:
   - `https://www.nrc.gov/docs/ML2202/ML22027A362.pdf`
   - `https://www.nrc.gov/docs/ML1521/ML15219A611.pdf`
   - `https://www.nrc.gov/docs/ML1216/ML12166A406.pdf`
   - `https://www.nrc.gov/docs/ML0831/ML083150382.pdf`
   - Without these, `find_precedent_events` has exactly one record.
4. **Guide texts that make cross-family measurable.** The verified accessions are RG 1.3 Rev 2 ML16215A353, RG 1.4 Rev 2 ML16215A351 and RG 1.5 ML16215A352, from FR-81-88710 (`https://www.nrc.gov/docs/ML1621/ML16215A35{3,1,2}.pdf`). Their successors, RG 1.183 Rev 0 and RG 1.195 Rev 0, are also needed. For the pump-relevant chain: RG 1.1 Rev 0 and RG 1.82 Rev 4 and Rev 5.
   - The accessions for RG 1.183, RG 1.195, RG 1.1 and RG 1.82 are **not** verified anywhere on disk. Look them up on the NRC Division 1 index pages, which open in a browser.
   - Only with both sides' text in the corpus can `flag` and `graph` diverge at retrieval level, and even then only for as-of dates before the withdrawal (2015-04-30 or 2016-12-08).
5. **Label and metadata fixes (no download):**
   - (a) Set row 13's `stated_date` to 2015-04-30. Give rows 23, 26, 29 and 30 the FR publication date, marked inferred.
   - (b) Add RG 1.78 rows to `rg_index.jsonl`. This needs the "1.61–1.80" Division 1 sub-page read in a browser.
   - (c) Give FR rule and notice rows a non-authority role: either exclude them from `governs`, or set `effective_to` to the next amendment of the same section. As things stand, the 1999 text inside FR-64-53582 "governs" today in `graph` mode (1.9).
   - (d) Give the four undated rows (1.8) inferred publication dates.
   - (e) Consider adding the 1-1-2000 CFR edition as a row (issue 2000-01-01, effective 2001-03-13). It is the cleanest real "printed before it took effect" document for a recency baseline.
6. **Facility-side documents for the anchor:** Ford Nuclear Reactor TS Amendment 44 and IR 50-002/98202 (March 24, 1998). These would close the loop "pump change → TS change required". No accession is verified, so an ADAMS search is needed (an `ADAMS_API_KEY` would unblock it; ACCESS_REPORT confirms the API answers 401 without a key).
7. **RG 1.84 Rev 32 and RG 1.85 Rev 31 PDFs**, to settle the 2003-versus-2004 conflict (2.10). ML003739376 on the index is DG-1049, the *draft*, not the final Rev 31.
8. **Remove neighbouring FR documents from chunks** (1.11). Crop each FR PDF to its own FR Doc boundaries, or record a page/column range in the manifest, so that SBA, airworthiness-directive and Vermont Yankee text cannot be cited as NRC rule text.

---

### Tally

The root cause of most FAILs is the same two data gaps: the pre-1996 50.59 version is not recorded, and no regulatory guide text is on disk.

- **PASS (23):**
  - Manifest: 1.1–1.6 (6)
  - Facts: 2.1–2.8 and 2.11 (9)
  - Labels: 3.1 and 3.3 (2)
  - Real effective-after-issue cases: 4.2-A, B and C (3)
  - Resolves: 10CFR50.59 as of 2000-06-01, 2001-03-13 and 2026-11-14 (3)
- **FAIL (8):**
  - 2.9: no version covers the anchor date
  - 3.4: `stated_date` null on row 13, which permanently blocks RG 1.1 (rows 23, 26, 29 and 30 are masked by the corroboration rule)
  - 4.1: anchor question as of 1996, governing-text row
  - 4.2-E/F: cross-family not measurable
  - 4.2-G: no partial case
  - 4.2-H: no effective-before-issue case
  - 4.4 / L3: RG 1.78 missing, so `resolve RG-1.95` returns a silent NOTHING
  - 5: `resolve 10CFR50.59 --as-of 1996-04-01` gives NOTHING without naming the gap
- **WARN (25):**
  - Manifest: 1.7–1.12 (6)
  - Facts: 2.10 and 2.12–2.14 (4)
  - Labels: 3.2 and 3.5–3.7 (4)
  - Anchor: 4.1, the other three sub-rows (3)
  - 4.2-D (1)
  - 4.3: metrics, and question count (2)
  - The RG-1.1 resolve (1)
  - Edges: L2, L5, L6 and L7 (4)
  - L1 and L4 are counted under 3.4. L3 is counted under 4.4.
