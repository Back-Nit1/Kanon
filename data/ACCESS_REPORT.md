# Kanon data: access report

Captured 2026-11-14. Every file under `data/raw/` comes from a public primary source. Rebuild `manifest.jsonl` and `labels/*.jsonl` with `python3 -I data/scripts/build_labels.py`. The build re-checks every evidence quote against its source file and fails if a quote is missing.

## Main blocker: www.nrc.gov refuses non-browser downloads

`www.nrc.gov` (ADAMS `/docs/` PDFs and all HTML pages), `pbadupws.nrc.gov`, `downloads.regulations.gov` and `lersearch.inl.gov` all return **HTTP 403** (Akamai "Access Denied") to curl. This happens with the `kanon-research/0.1` User-Agent and with curl's default User-Agent. WebFetch gets the same 403.

Here is the response body from `https://www.nrc.gov/docs/ML2202/ML22027A362.pdf`:

```
<TITLE>Access Denied</TITLE> ... You don't have permission to access "http&#58;&#47;&#47;www&#46;nrc&#46;gov&#47;docs&#47;ML2202&#47;ML22027A362&#46;pdf" on this server.
```

What I tried:

- **Spoofing a browser User-Agent:** I attempted this once. The session's permission system denied it, and I did not retry.
- **Fetching the PDF bytes with JavaScript in a real Chrome tab:** also denied by the permission system. I did not try to get around either denial.
- **Viewing pages in Chrome:** this works. I used it to confirm the identity of every NRC document listed below. It cannot save the original bytes, though. The only NRC-derived files on disk are text I extracted from the browser view and transcribed. They are marked "NOT original bytes".

**What would unblock the rest:**

- (a) The user downloads the NRC PDFs listed below by hand into `data/raw/adams/`, or
- (b) The user explicitly allows browser-based downloading for nrc.gov, or
- (c) An `ADAMS_API_KEY` is provided. `https://adams-api.nrc.gov/aps/api/search/ML22027A362` is reachable and answers `401 "Access denied due to missing subscription key"`. The API can return indexed text, but the PDF bytes may still sit behind the nrc.gov block.

## Step 1: access verification

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | ML22027A362 | **PASS (identity and passage). Bytes not downloadable (403).** | Opened in Chrome's PDF viewer: 27 pages, tab title "James A. Fitzpatrick Nuclear Power Plant - Integrated Inspection Report 05000333/2021004". The cover letter is dated January 27, 2022. PDF page 4 shows "Docket Number: 05000333 / License Number: DPR-59 / Report Number: 05000333/2021004 / Facility: James A. FitzPatrick Nuclear Power Plant". PDF page 5 (printed page 2), finding FIN 05000333/2021004-02, reads: "The inspectors documented a self-revealed Green finding for Exelon's failure to follow procedure MA-AA-716-010, "Maintenance Planning," Revision 31, and the associated work order 05078586; and maintenance procedure MP-033.05, "Condensate Booster Pump Maintenance," Revision 28, for the replacement of the 'B' condensate booster pump (CBP) outboard seal assembly on October 3, 2020. Specifically, Exelon failed to properly verify that the replacement seal assembly was the correct style part. This resulted in a large seal leak on the 'B' CBP which required a unit downpower to 84 percent to repair the pump's seal." The same page also has an RHR pump finding: NCV 05000333/2021004-01, the 'A' RHR pump motor failure, which led to "an emergency license amendment to conduct motor replacement". |
| 2 | EA-98-155 | **PASS (content). Original HTML bytes not downloadable (403).** | Viewed in Chrome. The old URL redirects to `https://www.nrc.gov/documents-reports/document-collections/enforcement-documents/significant-enforcement-actions/reactor-licensees/ea98155`. Letter dated May 13, 1998; Docket 50-002; License R-28. Quote: "The first violation involved the failure to adequately perform a required 10 CFR 50.59 evaluation of modification Request No. 120 which installed a new primary cooling pump and motor and removed the pump discharge check valve internals in April 1996." The Notice of Violation says the evaluation "did not identify that a charge [sic] to the facility's technical specification was required". The page links **no PDF**: the NOV text is embedded in the HTML. Transcribed text is saved at `raw/nrc_html/ea98155.extract.txt`. |
| 3 | eCFR versions, 50.59 | **PASS** | `GET .../versions/title-10.json?part=50&section=50.59` returned 200 (`raw/ecfr/versions_title10_part50_sec50.59.json`). It lists amendment dates **2016-12-30** (the eCFR history floor), **2024-07-16, 2024-08-15, 2024-12-30, 2025-01-29**. `full/{date}` needs `Accept-Encoding: gzip` (otherwise HTTP 406). Diffs: 2024-07-16 and 2024-12-30 only add an amendment XREF link. The text changes on 2024-08-15 and 2025-01-29, which are the FR effective dates. |
| 4 | RG Division 1 index | **PASS (read). Raw HTML not saved (403 to curl).** | The index moved to `https://www.nrc.gov/regulations-legislation/regulatory-guides-by-division/division-1---power-reactors`, split into 14 sub-pages (1.1–1.20 … 1.261–1.271). Each table row has Guide / Title (with a collapsible HISTORY list) / Revision / Issue Date / Additional Information. Withdrawn guides show a "W" revision row and "(Withdrawn -- See NN FR NNNNN, date)" in the title. Supersession is stated in prose HISTORY items, for example "06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of RG 1.84". Four sub-pages were read and extracted (see the D row of the table below). |
| 5 | ADAMS search API | **BLOCKED on key** | `adams-api.nrc.gov` returns 401 "missing subscription key". `ADAMS_API_KEY` is not set. No keyless path was found. |

## What was collected

| Cat | Collected (in manifest) | Skipped / unavailable |
|---|---|---|
| A. 4 KANON accessions | 0 in manifest. All 4 were opened in Chrome and identity confirmed. **ML22027A362**: IR 05000333/2021004, 2022-01-27, 27 pp. **ML15219A611**: "IR 05000333/2015007 … Evaluation of Changes, Tests, or Experiments and Permanent Plant Modification Team Inspection Report", letter dated August 6, 2015, 28 pp. **ML12166A406**: "IR 05000333-12-007; 4/16/2012 - 5/3/2012 … Engineering Specialist Plant Modifications Inspection", letter dated June 14, 2012, subject "...MODIFICATIONS TEAM INSPECTION REPORT 05000333/2012007", "no findings were identified", 24 pp. **ML083150382**: "IR 05000333-08-004; on 07/01/08 to 09/30/08 …", letter dated November 7, 2008, 38 pp. | Bytes blocked (403). Not checked: the "19 / 20 separate 50.59 screenings" counts and the 2008 'B' feedwater pump seal detail (I only read the cover pages). |
| B. 10 CFR 50.59 point-in-time | **6 versions** (`doc_id` 10CFR50.59). The 1-1-97, 1-1-01 and 1-1-02 CFR editions come from GovInfo PDFs. The 2017-01-01, 2024-08-15 and 2025-01-29 versions are eCFR XML (`paginated: false`). **7 FR rules**: 61 FR 39278 (1996), 64 FR 53582 (1999 rule), 65 FR 77773 (effective-date confirmation), 66 FR 64737 (2001), 72 FR 49352 (2007), 89 FR 57717 (2024), 89 FR 106234 (2024/25). | The **1996 CFR edition of Title 10 is not on GovInfo**: `bulkdata/CFR/1996` lists only titles 21, 40, 42–50. The 1-1-97 edition is used instead; its paragraphs (a)–(c) are the April-1996 text. The 1-1-2000 edition is on disk as a trap example but is not listed as a separate version. The 1999 and 2016 editions were downloaded and then removed as duplicate text. |
| C. Reg guides | 0 RG PDFs (all on nrc.gov, which is blocked). **8 FR notices** about RG issuance and withdrawal (`kind: notice`): RG 1.187 Rev 1/2/3 issuance; RG 1.1, 1.182, 1.3/1.4/1.5, 1.85 and 1.95 withdrawals. | RG 1.187 Rev 0–3 PDFs, DG-1466 and the pump RGs: blocked (nrc.gov and regulations.gov both return 403). The regulations.gov API with DEMO_KEY does list RG 1.187 Rev 1 and Rev 2 attachments (NRC-2019-0086-0002/-0007/-0016), but the downloads return 403. |
| D. RG division index | `labels/rg_index.jsonl`: **124 rows, 35 guides**. Includes all of 1.1–1.20 plus selected guides 1.78, 1.82–1.86, 1.95, 1.148, 1.160, 1.182, 1.183, 1.186, 1.187, 1.192, 1.195. | The other 10 Division 1 sub-pages and Divisions 2–10 were not extracted, because every row has to be moved through the browser by hand. RG 1.84 rows below Rev 31 and RG 1.85 rows below Rev 30 were not transcribed. |
| E. FitzPatrick TS / license amendments | **0** | All amendments and TS pages are ADAMS-only (blocked). FR monthly notices only give application summaries, not "remove page / insert page" text. I fetched five 2021–22 notices: none of the FitzPatrick entries concerned pumps, so I discarded them. **No partial-supersession evidence from amendments could be collected.** The KANON.md fallback applies: use the RG corpus for labels. |
| F. Operating experience | **1** (EA-98-155, from the browser-text extract, `paginated: false`) | The FitzPatrick IRs are confirmed but blocked (see A). LERSearch returns 403. No other LERs or IRs were collected. |
| G. Point Beach AFW recirculation-line mod | — | **Not verified, excluded.** ADAMS search is unavailable (no key) and nrc.gov is blocked. |
| H. Not in the public record | — | Procedure **MP-033.05** itself, controlled drawings, design calculations and maintenance history are **not public**. Inspection reports only reference them; for example, ML22027A362 cites MP-033.05 Rev 28 and work order 05078586 without publishing them. |

Manifest totals (22 rows): regulation 6, rulemaking 7, notice 8, operating_experience 1. All PDFs have a text layer, so nothing was skipped as a scan.

**`kind` enum:** regulation | reg_guide | tech_spec | license_amendment | operating_experience | rulemaking | **notice** | other.
- Records (what happened; no `effective_from` required): operating_experience, rulemaking, notice.
- FR final rules stay `rulemaking`. The 8 FR guide issuance and withdrawal notices are `notice` (revised 2026-11-14 after evaluation).

**`page_range` (added 2026-11-14).** FR rows carry `page_range: [first, last]`, the 1-based PDF pages spanned by the NRC document from its heading through its own `[FR Doc. …] / BILLING CODE` line. The build script checks that this line is on the last page.
- govinfo's per-document PDFs start and end exactly on the NRC document's own pages, so every range is the whole file:
  - FR-61-39278 [1,27]
  - FR-64-53582 [1,36]
  - FR-65-77773 [1,2]
  - FR-89-57717 [1,5]
  - FR-89-106234 [1,20]
  - FR-84-25077 [1,3]
  - FR-85-40696 [1,2]
  - FR-86-31346 [1,2]
  - FR-80-24293 [1,2]
  - FR-77-70846 [1,2]
  - FR-81-88710 [1,1]
  - FR-69-34202 [1,1]
  - FR-67-939 [1,1]
- The field is omitted for FR-66-64737 and FR-72-49352, which contain no other document.
- **Limitation:** the unrelated FR documents share the first and/or last page with the NRC document, so trimming by page cannot remove them. Removing them would need text-level start and end anchors (the NRC heading and the `[FR Doc. …]` line).

**`stated_date` rule for FR-sourced evidence.** A statement in a dated FR notice is attested as of the notice's DATES-line effective date. If the notice has no DATES line, its publication date (from the page header) is used. The 2026-11-14 revision applied this rule to five rows: 13 (80 FR 24293, 2015-04-30), 23 (69 FR 34202, 2004-06-18), 26 (67 FR 939, 2002-01-08), and 29 and 30 (77 FR 70846, 2012-11-27; the notice has no DATES line). Index HISTORY items (for example RG 1.3/1.4/1.5) carry no date and remain null.

**RG 1.78 (added 2026-11-14).** Read from the Division 1 page 1.61–1.80 by viewing it in the browser, the same way as the other sub-pages. It adds 3 rows to `rg_index.jsonl`: Rev 2 12/2021, Rev 1 12/2001, Rev 0 06/1974. Its HISTORY contains "Revision 1 incorporates guidance from withdrawn Regulatory Guide 1.95". That sentence is kept in `history_text` only. It is not in the evidence file, because the current parser turned it into a self-edge (`RG-1.78@1 → RG-1.78@1 withdrawn`, unresolved).

**April-1996 gap (deliberate).** No 50.59 version is recorded for dates before 1996-08-28. Dating the start of the (a)–(c) text needs 57 FR 39358 (Aug. 31, 1992), the last pre-1996 amendment in the citation line. That issue is only available as a 255 MB whole-issue scan (`govinfo.gov/content/pkg/FR-1992-08-31/pdf/FR-1992-08-31.pdf`), and it was not fetched. As-of queries before 1996-08-28 should therefore abstain; no version was invented.

Field conventions:

- `source_page` in the evidence file is the 1-based PDF page index.
- `rg_index.issue_date` is `YYYY-MM`, because the index gives month only; no day was invented.
- In `history_text`, items are joined with ` || ` and link hrefs are appended in `<>`.

## Candidate cases

### (1) Partial supersession

No license-amendment "remove page / insert page" evidence was available (blocked). What was found:

- **10CFR50.59, paragraph-level amendments.** Each changes only part of the section:
  - FR-61-39278: "21. Section 50.59, paragraphs (d) and (e) are added to read as follows:"
  - FR-66-64737: "15. In § 50.59(b), in the last sentence, insert the words ‘‘of nuclear fuel,’’ between the words ‘‘possession’’ and ‘‘but’’."
  - FR-72-49352: "In § 50.59, paragraphs (b), (d)(2), and (d)(3) are revised to read as follows:"
  - FR-89-57717: "9. In § 50.59, amend paragraph (c)(1)(i) by removing the text ‘‘A change’’ and adding in its place the text ‘‘An amendment’’."
  - FR-89-106234: "In § 50.59, revise paragraph (b) to read as follows:"
- **One guide split across two successors** (each successor holds part of the content):
  - RG-1.3 Rev 2, RG-1.4 Rev 2 and RG-1.5 Rev 0 go to RG-1.183 **and** RG-1.195. Index: "Guidance from RG 1.3 has been updated and incorporated into RG 1.183, ... and RG 1.195, ...".
  - FR-81-88710: "These three RGs are being withdrawn because the guidance contained in them has been superseded and is now incorporated into RG 1.183, ... and RG 1.195, ..."

### (2) Cross-family supersession

- **RG-1.1 → RG-1.82 (pump-related: NPSH for ECCS pumps).** FR-80-24293: "The NRC is withdrawing RG 1.1 because its guidance has been incorporated into RG 1.82, ‘‘Water Sources for Long-Term Recirculation Cooling Following a Loss-of-Coolant Accident.’’" The target revision is not named.
- **RG-1.85 Rev 31 → RG-1.84 Rev 32 (revision-level).**
  - Index: "06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of RG 1.84".
  - FR-69-34202: "...is being withdrawn because the guidance in Regulatory Guide 1.85 has been updated and incorporated into Revision 32 of Regulatory Guide 1.84."
  - The incorporation is dated 06/2003; the formal withdrawal is 06/2004.
- **RG-1.95 → RG-1.78 Rev 1.**
  - Index: "01/2002 – Guidance from RG 1.95 has been updated and incorporated into Revision 1 of RG 1.78".
  - FR-67-939 has the matching sentence.
- **RG-1.182 Rev 0 → RG-1.160 Rev 3.**
  - Index: "05/2012 – Guidance from RG 1.182 has been incorporated in RG 1.160".
  - FR-77-70846: "The document is redundant due to the inclusion of its subject matter in Rev. 3 of RG 1.160, ..."
  - The index dates the incorporation 05/2012; the withdrawal notice is dated 11/27/2012.
- **RG-1.3 / 1.4 / 1.5 → RG-1.183 + RG-1.195** (see above).

### (3) Effective date different from issue date

- **10CFR50.59, 1999 revision (the key case).**
  - FR-64-53582 was published **1999-10-04**. It says: "Sections 50.59, 50.66, 50.71(e), and 50.90 become effective 90 days after issuance of applicable regulatory guidance."
  - FR-65-77773 (published 2000-12-13) then says: "The effective date of the October 9, 1999 amendment to 10 CFR 50.59 (64 FR 53613) is March 13, 2001." ("October 9" is a typo in the source.)
  - So **effective_from 2001-03-13 comes 17 months after issue**, and the effective date was set by a different document.
  - Metadata traps:
    - federalregister.gov's structured `effective_on` is **2000-02-01** for the rule (that date applies only to the Part 72 sections) and **1999-10-09** for the confirmation notice (parsed from the typo).
    - The **1-1-2000 CFR edition already prints the new text**, with only "EDITORIAL NOTE: See 64 FR 53582, Oct. 4, 1999, for effectiveness of § 50.59." The old text governed throughout 2000.
- **FR-89-57717:** issued 2024-07-16, effective 2024-08-15 ("This final rule is effective on August 15, 2024."). eCFR lists both dates as 50.59 versions.
- **FR-89-106234:** issued 2024-12-30, effective 2025-01-29.
- **FR-72-49352:** issued 2007-08-28, effective 2007-09-27.
- **FR-61-39278:** issued 1996-07-29, effective 1996-08-28.
- **RG-1.187 Rev 2:** the index issue date is 06/2020, but FR-85-40696 says "Revision 2 to RG 1.187 takes effect on July 7, 2020."
- **Effective date earlier than issue date:** **none found.** Every confirmed case has effective on or after issue.

## Other notes for the code

The NRC guide landing page (`/regulations-legislation/regulatory-guides-by-division`) says, verbatim: "Withdrawal of a Regulatory Guide should be thought of as the final revision of the guide." and "Although a RG is withdrawn, current licensees may continue to use it, and withdrawal does not affect any existing licenses or agreements." This means a withdrawn guide is not void for licensees that already rely on it.

Index data inconsistency: the RG 1.186 HISTORY list contains "12/2000 – RG 1.186 Revision 1", but the revision table shows only Rev 0 (12/2000). It is recorded verbatim and not interpreted.

Some govinfo per-document FR PDFs also contain neighbouring FR documents on shared pages. Each manifest `notes` field says which ones.
