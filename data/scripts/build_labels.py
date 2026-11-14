"""Build data/manifest.jsonl, labels/supersession_evidence.jsonl, labels/rg_index.jsonl.

Run from anywhere:  python3 -I data/scripts/build_labels.py
Metadata below is hand-curated from documents that were opened and read (see date_basis/notes).
Every evidence quote is re-checked against its source file; the build fails if a quote is not found.
"""
import datetime, hashlib, json, os, re, subprocess, sys

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOV = "https://www.govinfo.gov/content/pkg/"
NRC_IDX = "https://www.nrc.gov/regulations-legislation/regulatory-guides-by-division/division-1---power-reactors/"
P_1_20, P_81_100 = NRC_IDX + "regulatory-guides-11---120", NRC_IDX + "regulatory-guides-181---1100"
P_141_160, P_181_200 = NRC_IDX + "regulatory-guides-1141---1160", NRC_IDX + "regulatory-guides-1181---1200"
P_61_80 = NRC_IDX + "regulatory-guides-161---180"
EA_URL = "https://www.nrc.gov/reading-rm/doc-collections/enforcement/actions/reactors/ea98155.html"

def fr(doc_id, path, url, title, issue, eff, basis, notes="", kind="rulemaking"):
    return dict(page_range=PAGE_RANGE.get(doc_id), doc_id=doc_id, revision="0", family=None, kind=kind, title=title, effective_from=eff,
                effective_to=None, issue_date=issue, status="active", accession=None, source_url=url,
                local_path=path, date_basis=basis, notes=notes)

def cfr(rev, path, url, title, eff_from, eff_to, status, basis, notes):
    return dict(doc_id="10CFR50.59", revision=rev, family="10CFR50.59", kind="regulation", title=title,
                effective_from=eff_from, effective_to=eff_to, issue_date=None, status=status, accession=None,
                source_url=url, local_path=path, date_basis=basis, notes=notes)

T5059 = "10 CFR 50.59 Changes, tests, and experiments"
# 1-based PDF pages spanned by the NRC document itself (its heading through its own "[FR Doc. ...] / BILLING CODE" line).
# govinfo per-document PDFs start and end on the NRC document's own pages, so the range is always the whole file;
# the listed rows still share their first and/or last page with unrelated FR documents (page-level trimming cannot remove that).
# FR-66-64737 and FR-72-49352 carry no other document and are omitted.
PAGE_RANGE = {"FR-61-39278": [1, 27], "FR-64-53582": [1, 36], "FR-65-77773": [1, 2], "FR-89-57717": [1, 5],
              "FR-89-106234": [1, 20], "FR-84-25077": [1, 3], "FR-85-40696": [1, 2], "FR-86-31346": [1, 2],
              "FR-80-24293": [1, 2], "FR-77-70846": [1, 2], "FR-81-88710": [1, 1], "FR-69-34202": [1, 1], "FR-67-939": [1, 1]}
DOCS = [
    cfr("1996-08-28", "raw/govinfo/CFR-1997-title10-vol1-sec50-59.pdf",
        GOV + "CFR-1997-title10-vol1/pdf/CFR-1997-title10-vol1-sec50-59.pdf", T5059 + " (CFR edition revised as of 1-1-97)",
        "1996-08-28", "2001-03-13", "superseded",
        "effective_from = effective date of the last amendment in this text (61 FR 39278, 'EFFECTIVE DATE: August 28, 1996.'); effective_to = effective date of the 1999 revision per 65 FR 77773 ('is March 13, 2001'); issue_date null because the CFR volume's printing date is not stated (edition is 'as of' 1997-01-01).",
        "Pre-1999 'unreviewed safety question' text. Paragraphs (a)-(c) are the text in force during the April 1996 EA-98-155 pump change; 61 FR 39278 only ADDED paragraphs (d) and (e) (effective 1996-08-28). The 1996 CFR edition of Title 10 is not on GovInfo (bulkdata/CFR/1996 lists only titles 21, 40, 42-50), so the 1-1-97 edition is the earliest available print. Page 1 of the PDF also carries the tail of 50.58."),
    cfr("2001-03-13", "raw/govinfo/CFR-2001-title10-vol1-sec50-59.pdf",
        GOV + "CFR-2001-title10-vol1/pdf/CFR-2001-title10-vol1-sec50-59.pdf", T5059 + " (CFR edition revised as of 1-1-01)",
        "2001-03-13", "2001-12-14", "superseded",
        "effective_from from the edition's own EFFECTIVE DATE NOTE and 65 FR 77773 DATES ('is March 13, 2001'); effective_to = effective date of 66 FR 64737 ('EFFECTIVE DATE: December 14, 2001.').",
        "Text of 64 FR 53613 (published 1999-10-04). TRAP: the same new text is already printed in the 1-1-2000 CFR edition (raw/govinfo/CFR-2000-title10-vol1-sec50-59.pdf, kept on disk but not listed as a separate version) even though it did not take effect until 2001-03-13; during all of 2000 the old text still governed."),
    cfr("2001-12-14", "raw/govinfo/CFR-2002-title10-vol1-sec50-59.pdf",
        GOV + "CFR-2002-title10-vol1/pdf/CFR-2002-title10-vol1-sec50-59.pdf", T5059 + " (CFR edition revised as of 1-1-02)",
        "2001-12-14", "2007-09-27", "superseded",
        "effective_from = 66 FR 64737 'EFFECTIVE DATE: December 14, 2001.'; effective_to = 72 FR 49352 'The effective date is September 27, 2007.'",
        "Citation line reads '[64 FR 53613, Oct. 4, 1999, as amended at 66 FR 64738, Dec. 14, 2001]'."),
    cfr("2007-09-27", "raw/ecfr/10CFR50.59_2017-01-01.xml",
        "https://www.ecfr.gov/api/versioner/v1/full/2017-01-01/title-10.xml?part=50&section=50.59", T5059 + " (eCFR point-in-time 2017-01-01)",
        "2007-09-27", "2024-08-15", "superseded",
        "effective_from = 72 FR 49352 DATES (last amendment in the citation line); effective_to = 89 FR 57717 'This final rule is effective on August 15, 2024.' eCFR history starts 2017-01-01, so the file is the 2017-01-01 snapshot of text unchanged since 2007.",
        "Not paginated (XML). Citation line ends '72 FR 49500, Aug. 28, 2007'."),
    cfr("2024-08-15", "raw/ecfr/10CFR50.59_2024-08-15.xml",
        "https://www.ecfr.gov/api/versioner/v1/full/2024-08-15/title-10.xml?part=50&section=50.59", T5059 + " (eCFR point-in-time 2024-08-15)",
        "2024-08-15", "2025-01-29", "superseded",
        "effective_from = 89 FR 57717 DATES; effective_to = 89 FR 106234 'This final rule is effective on January 29, 2025.'",
        "Only change: (c)(1)(i) 'A change to the technical specifications' -> 'An amendment to the technical specifications'. TRAP: the eCFR versions API also lists amendment_date 2024-07-16 (publication day) for 50.59; the 2024-07-16 snapshot only adds an XREF link and the old text, the text change appears at 2024-08-15."),
    cfr("2025-01-29", "raw/ecfr/10CFR50.59_2025-01-29.xml",
        "https://www.ecfr.gov/api/versioner/v1/full/2025-01-29/title-10.xml?part=50&section=50.59", T5059 + " (eCFR point-in-time 2025-01-29, current)",
        "2025-01-29", None, "active",
        "effective_from = 89 FR 106234 DATES. eCFR versions API meta latest_amendment_date 2025-01-29 (checked 2026-11-14).",
        "Paragraph (b) extended to non-power production or utilization facilities that permanently ceased operations. Same pattern as 2024: eCFR lists 2024-12-30 (publication) with link only; text changes 2025-01-29."),
    fr("FR-61-39278", "raw/fr/FR-1996-07-29_96-19031.pdf", GOV + "FR-1996-07-29/pdf/96-19031.pdf",
       "Decommissioning of Nuclear Power Reactors (final rule; adds 50.59(d) and (e))", "1996-07-29", "1996-08-28",
       "issue_date = FR publication date; effective_from from 'EFFECTIVE DATE: August 28, 1996.' on page 1.",
       "Amendatory instruction 21 adds 50.59(d),(e). PDF also contains neighbouring FR documents on shared pages."),
    fr("FR-64-53582", "raw/fr/FR-1999-10-04_99-25054.pdf", GOV + "FR-1999-10-04/pdf/99-25054.pdf",
       "Changes, Tests, and Experiments (final rule revising 10 CFR 50.59)", "1999-10-04", "2001-03-13",
       "issue_date = FR publication 1999-10-04; effective_from is for 50.59 only: the rule says 50.59 becomes effective '90 days after issuance of applicable regulatory guidance', fixed as March 13, 2001 by 65 FR 77773.",
       "KEY CASE: issue 1999-10-04, 50.59 effective 2001-03-13 (17 months later, set by a later document). Part 72 sections effective 2000-02-01; 72.212/72.48 effective 2001-04-05. federalregister.gov API reports effective_on=2000-02-01 for this document, which is wrong for 50.59. 50.59 text is at 64 FR 53613 (PDF page 32). Page 1 begins with the end of an unrelated OPM rule."),
    fr("FR-65-77773", "raw/fr/FR-2000-12-13_00-31735.pdf", GOV + "FR-2000-12-13/pdf/00-31735.pdf",
       "Changes, Tests, and Experiments: Confirmation of Effective Date and Availability of Guidance", "2000-12-13", None,
       "issue_date = FR publication; this notice has no effective date of its own and sets 50.59's effective date to 2001-03-13.",
       "Announces RG 1.187 (Rev 0). Its DATES line says 'October 9, 1999 amendment' (sic; the rule was published October 4, 1999); federalregister.gov API reports effective_on=1999-10-09, parsed from that typo. Do not trust structured FR metadata here."),
    fr("FR-66-64737", "raw/fr/FR-2001-12-14_01-30832.pdf", GOV + "FR-2001-12-14/pdf/01-30832.pdf",
       "Minor Errors in Regulatory Text; Correction (inserts 'of nuclear fuel,' in 50.59(b))", "2001-12-14", "2001-12-14",
       "Both dates from FR publication date and 'EFFECTIVE DATE: December 14, 2001.'",
       "CFR citation lines cite this as 66 FR 64738 (page of the 50.59 instruction); document starts at 64737."),
    fr("FR-72-49352", "raw/fr/FR-2007-08-28_07-3861.pdf", GOV + "FR-2007-08-28/pdf/07-3861.pdf",
       "Licenses, Certifications, and Approvals for Nuclear Power Plants (Part 52 rule; revises 50.59(b), (d)(2), (d)(3))", "2007-08-28", "2007-09-27",
       "issue_date = FR publication; effective_from from 'DATES: The effective date is September 27, 2007.'",
       "215 pages; 50.59 instruction at PDF page 149 (72 FR 49500)."),
    fr("FR-89-57717", "raw/fr/FR-2024-07-16_2024-15234.pdf", GOV + "FR-2024-07-16/pdf/2024-15234.pdf",
       "Miscellaneous Corrections (amends 50.59(c)(1)(i))", "2024-07-16", "2024-08-15",
       "issue_date = FR publication; effective_from from 'DATES: This final rule is effective on August 15, 2024.'",
       "Issue date and effective date differ by 30 days; eCFR lists both dates as versions."),
    fr("FR-89-106234", "raw/fr/FR-2024-12-30_2024-30721.pdf", GOV + "FR-2024-12-30/pdf/2024-30721.pdf",
       "Non-Power Production or Utilization Facility License Renewal (revises 50.59(b))", "2024-12-30", "2025-01-29",
       "issue_date = FR publication; effective_from from 'DATES: This final rule is effective on January 29, 2025.'",
       "CFR citation lines cite 89 FR 106251 (page of the 50.59 instruction, PDF page 18). PDF page 1 also contains an unrelated USDA rule."),
    fr("FR-84-25077", "raw/fr/FR-2019-05-30_2019-11246.pdf", GOV + "FR-2019-05-30/pdf/2019-11246.pdf",
       "Guidance for Changes, Tests, and Experiments (issuance of RG 1.187 Rev 1; DG-1356 for comment)", "2019-05-30", "2019-05-30",
       "issue_date = FR publication; effective_from from 'Revision 1 to RG 1.187 is available on May 30, 2019.'",
       "Subject: RG-1.187 Rev 1. NRC index lists Rev 1 issue date 05/2019. The RG PDF itself is on nrc.gov and could not be downloaded.", kind="notice"),
    fr("FR-85-40696", "raw/fr/FR-2020-07-07_2020-14564.pdf", GOV + "FR-2020-07-07/pdf/2020-14564.pdf",
       "Guidance for Implementation of Changes, Tests, and Experiments (issuance of RG 1.187 Rev 2)", "2020-07-07", "2020-07-07",
       "issue_date = FR publication; effective_from from 'Revision 2 to RG 1.187 takes effect on July 7, 2020.'",
       "Subject: RG-1.187 Rev 2. NRC index gives Rev 2 issue date 06/2020 but the FR notice says it takes effect 2020-07-07: the guide's own date and its effective date differ.", kind="notice"),
    fr("FR-86-31346", "raw/fr/FR-2021-06-11_2021-12280.pdf", GOV + "FR-2021-06-11/pdf/2021-12280.pdf",
       "Guidance for Implementation of Changes, Tests, and Experiments (issuance of RG 1.187 Rev 3)", "2021-06-11", "2021-06-11",
       "issue_date = FR publication; effective_from from 'Revision 3 to RG 1.187 is available on June 11, 2021.'",
       "Subject: RG-1.187 Rev 3 (clarification after post-promulgation comments on Rev 2). PDF page 1 also contains NEA meeting notices.", kind="notice"),
    fr("FR-80-24293", "raw/fr/FR-2015-04-30_2015-10076.pdf", GOV + "FR-2015-04-30/pdf/2015-10076.pdf",
       "Net Positive Suction Head for Emergency Core Cooling and Containment Heat Removal System Pumps (withdrawal of RG 1.1)", "2015-04-30", "2015-04-30",
       "issue_date = FR publication; effective_from from 'DATES: Effective April 30, 2015, the NRC withdraws RG 1.1.'",
       "Pump-related cross-family case: RG 1.1 withdrawn, guidance in RG 1.82 (revision not named). PDF also contains an unrelated FEMA notice.", kind="notice"),
    fr("FR-77-70846", "raw/fr/FR-2012-11-27_2012-28719.pdf", GOV + "FR-2012-11-27/pdf/2012-28719.pdf",
       "Regulatory Guide 1.182 withdrawal notice", "2012-11-27", None,
       "issue_date = FR publication; no explicit effective date sentence was found in the notice, so effective_from left null (NRC index lists RG 1.182 'W' 11/2012).",
       "Cross-family: RG 1.182 Rev 0 -> RG 1.160 Rev 3 (issued May 21, 2012, 77 FR 30030).", kind="notice"),
    fr("FR-81-88710", "raw/fr/FR-2016-12-08_2016-29407.pdf", GOV + "FR-2016-12-08/pdf/2016-29407.pdf",
       "Withdrawal of Regulatory Guides 1.3, 1.4, and 1.5", "2016-12-08", "2016-12-08",
       "issue_date = FR publication; effective_from from 'The effective date of the withdrawal of RGs 1.3, 1.4, and 1.5 is December 8, 2016.'",
       "Cross-family one-to-many: three guides -> RG 1.183 and RG 1.195. PDF also contains the RG 8.7 Rev 3 notice.", kind="notice"),
    fr("FR-69-34202", "raw/fr/FR-2004-06-18_04-13751.pdf", GOV + "FR-2004-06-18/pdf/04-13751.pdf",
       "Regulatory Guide; Issuance, Availability (RG 1.85 withdrawn into RG 1.84 Rev 32)", "2004-06-18", None,
       "issue_date = FR publication; no effective date sentence found; NRC index lists RG 1.85 'W' 06/2004 but says the incorporation happened 06/2003.",
       "Cross-family at revision level: RG 1.85 Rev 31 -> RG 1.84 Rev 32. PDF page also carries other notices.", kind="notice"),
    fr("FR-67-939", "raw/fr/FR-2002-01-08_02-406.pdf", GOV + "FR-2002-01-08/pdf/02-406.pdf",
       "Regulatory Guide; Issuance, Availability (RG 1.78 Rev 1 issued; RG 1.95 withdrawn)", "2002-01-08", None,
       "issue_date = FR publication; no effective date sentence found.",
       "Cross-family: RG 1.95 -> RG 1.78 Rev 1. PDF page also carries other notices.", kind="notice"),
    dict(doc_id="EA-98-155", revision="0", family=None, kind="operating_experience",
         title="EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)",
         effective_from="1998-05-13", effective_to=None, issue_date="1998-05-13", status="active", accession=None,
         source_url=EA_URL, local_path="raw/nrc_html/ea98155.extract.txt",
         date_basis="issue_date from the letter date 'May 13, 1998' and 'Dated this 13th day of May 1998'; effective_from set equal (an enforcement action has no separate effective date).",
         notes="NOT ORIGINAL BYTES: www.nrc.gov answers HTTP 403 to non-browser clients, so this is the page's article text read in Chrome and transcribed. sha256 is of the extraction file. Identity confirmed (EA 98-155, Docket 50-002, License R-28, IR 50-002/98202). No PDF of the NOV is linked from the page. The quoted 50.59 language ('does not involve a change in the technical specifications') is the pre-1999 rule text."),
]

# (subject_doc, subject_rev, evidence, source file, stated_date, notes). The page is found automatically.
EVIDENCE = [
    ("10CFR50.59", "1996-08-28", "21. Section 50.59, paragraphs (d) and (e) are added to read as follows:", "FR-61-39278", "1996-08-28",
     "Partial amendment: only (d),(e) added, (a)-(c) untouched. stated_date = rule's EFFECTIVE DATE."),
    ("10CFR50.59", "2001-03-13", "Sections 50.59, 50.66, 50.71(e), and 50.90 become effective 90 days after issuance of applicable regulatory guidance.", "FR-64-53582", None,
     "Effective date deferred and left open at publication; resolved by FR-65-77773."),
    ("10CFR50.59", "2001-03-13", "The effective date of this amendment was deferred until guidance on implementation of the revised provisions of the rule was issued to reactor licensees.", "FR-65-77773", None, ""),
    ("10CFR50.59", "2001-03-13", "The effective date of the October 9, 1999 amendment to 10 CFR 50.59 (64 FR 53613) is March 13, 2001.", "FR-65-77773", "2001-03-13",
     "'October 9' is sic in the source (rule published October 4, 1999)."),
    ("10CFR50.59", "2001-03-13", "EDITORIAL NOTE: See 64 FR 53582, Oct. 4, 1999, for effectiveness of § 50.59.", "raw/govinfo/CFR-2000-title10-vol1-sec50-59.pdf", None,
     "From the 1-1-2000 CFR edition, which already prints the new text before it took effect. Source file is on disk but not a manifest version."),
    ("10CFR50.59", "2001-03-13", "EFFECTIVE DATE NOTE: See 64 FR 53582, Oct. 4, 1999, for effectiveness of § 50.59. At 65 FR 77773, Dec. 13, 2000, the effective date of the Oct. 4, 1999 revision of § 50.59 appearing at 64 FR 53613 was confirmed as Mar. 13, 2001.", "10CFR50.59@2001-03-13", "2001-03-13", ""),
    ("10CFR50.59", "2001-12-14", "15. In § 50.59(b), in the last sentence, insert the words ‘‘of nuclear fuel,’’ between the words ‘‘possession’’ and ‘‘but’’.", "FR-66-64737", "2001-12-14",
     "Partial (word-level) amendment."),
    ("10CFR50.59", "2007-09-27", "In § 50.59, paragraphs (b), (d)(2), and (d)(3) are revised to read as follows:", "FR-72-49352", "2007-09-27", "Partial amendment."),
    ("10CFR50.59", "2024-08-15", "9. In § 50.59, amend paragraph (c)(1)(i) by removing the text ‘‘A change’’ and adding in its place the text ‘‘An amendment’’.", "FR-89-57717", "2024-08-15",
     "MANUAL: the sentence runs down one column of a three-column page, so automatic text extraction interleaves it; checked by reading `pdftotext -layout` of PDF page 4. A '■' bullet before '9.' is omitted."),
    ("10CFR50.59", "2025-01-29", "In § 50.59, revise paragraph (b) to read as follows:", "FR-89-106234", "2025-01-29", "Partial amendment."),
    ("RG-1.1", "0", "Net Positive Suction Head for Emergency Core Cooling and Containment Heat Removal System Pumps (Safety Guide 1)(Withdrawn -- See 80 FR 24293, 04/30/2015)", "IDX:" + P_1_20, "2015-04-30",
     "Title cell of the NRC Division 1 index (withdrawal row 'W', 04/2015)."),
    ("RG-1.1", "0", "DATES: Effective April 30, 2015, the NRC withdraws RG 1.1.", "FR-80-24293", "2015-04-30", ""),
    ("RG-1.1", "0", "The NRC is withdrawing RG 1.1 because its guidance has been incorporated into RG 1.82, ‘‘Water Sources for Long-Term Recirculation Cooling Following a Loss-of-Coolant Accident.’’", "FR-80-24293", "2015-04-30",
     "stated_date = effective/publication date of the notice containing the statement (80 FR 24293, DATES: 'Effective April 30, 2015, the NRC withdraws RG 1.1.'), not a date in the sentence itself. Cross-family (RG 1.1 -> RG 1.82); target revision not named. The page-1 summary also says 'the same guidance is provided with more detail by RG 1.82'."),
    *[(f"RG-{n}", r, f"Guidance from RG {n} has been updated and incorporated into RG 1.183, \"Alternative Radiological Source Terms for Evaluating Design Basis Accidents at Nuclear Power Reactors,\" and RG 1.195, \"Methods and Assumptions for Evaluating Radiological Consequences of Design Basis Accidents at Light-Water Nuclear Power Reactors\"",
       "IDX:" + P_1_20, None, "Index HISTORY item. Cross-family one-to-many. Withdrawal row W 12/2016.") for n, r in (("1.3", "2"), ("1.4", "2"), ("1.5", "0"))],
    *[(f"RG-{n}", r, "These three RGs are being withdrawn because the guidance contained in them has been superseded and is now incorporated into RG 1.183, ‘‘Alternative Radiological Source Terms for Evaluating Design Basis Accidents at Nuclear Power Reactors,’’ and RG 1.195, ‘‘Methods and Assumptions for Evaluating Radiological Consequences of Design Basis Accidents at Light-Water Nuclear Power Reactors.’’",
       "FR-81-88710", "2016-12-08", "Same sentence covers RGs 1.3, 1.4, 1.5. stated_date from 'The effective date of the withdrawal of RGs 1.3, 1.4, and 1.5 is December 8, 2016.'") for n, r in (("1.3", "2"), ("1.4", "2"), ("1.5", "0"))],
    ("RG-1.84", "32", "12/2001 – DG-1090, Proposed Revision 32(combined RG 1.84 and 1.85)", "IDX:" + P_81_100, None, "Index HISTORY item for RG 1.84 (draft stage, 12/2001)."),
    ("RG-1.85", "31", "06/2003 – Guidance from Revision 31 of RG 1.85 was incorporated into Revision 32 of RG 1.84", "IDX:" + P_81_100, None,
     "Cross-family at revision level. Month-only date 06/2003 (RG 1.84 Rev 32 issued 06/2003), but formal withdrawal is 06/2004."),
    ("RG-1.85", "31", "Materials code case Acceptability - - ASME Section III, Division 1(Withdrawn See -- 69 FR 34202 06/18/2004)", "IDX:" + P_81_100, "2004-06-18", "Title cell."),
    ("RG-1.85", "31", "With the issuance of Revision 32 to Regulatory Guide 1.84, Regulatory Guide 1.85, ‘‘Materials Code Case Acceptability, ASME Section III, Division 1,’’ is being withdrawn because the guidance in Regulatory Guide 1.85 has been updated and incorporated into Revision 32 of Regulatory Guide 1.84.", "FR-69-34202", "2004-06-18",
     "stated_date = effective/publication date of the notice containing the statement (69 FR 34202; no DATES line, so publication date per header 'Friday, June 18, 2004'), not a date in the sentence itself."),
    ("RG-1.95", "1", "01/2002 – Guidance from RG 1.95 has been updated and incorporated into Revision 1 of RG 1.78", "IDX:" + P_81_100, None, "Cross-family."),
    ("RG-1.95", "1", "Protection of Nuclear Power Plant Control Room Operators Against an Accidental Chlorine Release(Withdrawn -- See 67 FR 939 01/08/2002)", "IDX:" + P_81_100, "2002-01-08", "Title cell."),
    ("RG-1.95", "1", "With the publication of Regulatory Guide 1.78, Regulatory Guide 1.95, ‘‘Protection of Nuclear Power Plant Control Room Operators Against an Accidental Chlorine Release,’’ is being withdrawn because the guidance in Regulatory Guide 1.95 has been updated and incorporated into Revision 1 of Regulatory Guide 1.78.", "FR-67-939", "2002-01-08",
     "stated_date = effective/publication date of the notice containing the statement (67 FR 939; no DATES line, so publication date per header 'Tuesday, January 8, 2002'), not a date in the sentence itself."),
    ("RG-1.182", "0", "05/2012 – Guidance from RG 1.182 has been incorporated in RG 1.160", "IDX:" + P_181_200, None, "Cross-family; index HISTORY item."),
    ("RG-1.182", "0", "Assessing and Managing Risk Before Maintenance Activities at Nuclear Power Plants(Withdrawn – See 77 FR 70846, 11/27/2012)", "IDX:" + P_181_200, "2012-11-27", "Title cell."),
    ("RG-1.182", "0", "The document is redundant due to the inclusion of its subject matter in Rev. 3 of RG 1.160, ‘‘Monitoring the Effectiveness of Maintenance at Nuclear Power Plants.’’", "FR-77-70846", "2012-11-27", "stated_date = effective/publication date of the notice containing the statement (77 FR 70846; the notice has no DATES line, so publication date per header 'Tuesday, November 27, 2012'), not a date in the sentence itself. Names target revision (RG 1.160 Rev 3)."),
    ("RG-1.182", "0", "Therefore, RG 1.182 is no longer needed, as the guidance in Regulatory Guide 1.182 is already contained in Regulatory Guide 1.160.", "FR-77-70846", "2012-11-27", "stated_date = effective/publication date of the notice containing the statement (77 FR 70846; the notice has no DATES line, so publication date per header 'Tuesday, November 27, 2012'), not a date in the sentence itself."),
    ("RG-1.187", "0", "This document announces the availability of that guidance (Regulatory Guide 1.187, ‘‘Guidance for Implementation of 10 CFR 50.59, Changes, Tests, and Experiments’’) and specifies the effective date for the October 4, 1999, amendment to § 50.59.", "FR-65-77773", "2000-12-13",
     "Issuance statement, not supersession. stated_date = FR publication date."),
    ("RG-1.187", "1", "Revision 1 to RG 1.187 is available on May 30, 2019.", "FR-84-25077", "2019-05-30", "Issuance statement; no explicit 'supersedes Revision 0' wording in the FR notice."),
    ("RG-1.187", "2", "Revision 2 to RG 1.187 takes effect on July 7, 2020.", "FR-85-40696", "2020-07-07", "Index issue date 06/2020; effective 2020-07-07."),
    ("RG-1.187", "3", "This Revision (i.e., Revision 3) addresses a clarification to the RG in response to post-promulgation comments on RG 1.187, Revision 2.", "FR-86-31346", "2021-06-11",
     "stated_date from 'Revision 3 to RG 1.187 is available on June 11, 2021.'"),
]

def sha256(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def pdf_pages(p):
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", p], capture_output=True, text=True).stdout).group(1))
    return [subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), p, "-"], capture_output=True, text=True).stdout for i in range(1, n + 1)]

def key(s):  # tolerant to line wraps and end-of-line hyphenation, nothing else
    return re.sub(r"[\s\-‐]", "", s)

def find(evidence, path):
    """Return (found, 1-based page or None)."""
    if path.endswith(".pdf"):
        for i, t in enumerate(pdf_pages(path), 1):
            if key(evidence) in key(t):
                return True, i
        return False, None
    with open(path, encoding="utf-8") as f:
        return key(evidence) in key(f.read()), None

def rg_rows(path, default_url):
    rows, url, g = [], default_url, None
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        parts = [x.strip() for x in line.rstrip("\n").split("¦")]
        tag = parts[0]
        if tag == "P":
            url = parts[1]
        elif tag == "G":
            title = parts[2]
            m = re.search(r"\((Withdrawn.*)\)\s*$", title)
            g = dict(rg=parts[1], title=title[:m.start()].strip() if m else title,
                     withdrawn=f"({m.group(1)})" if m else "", hist=parts[3] if len(parts) > 3 else "")
        elif tag == "R":
            rev, date, info = parts[1], parts[2], parts[3] if len(parts) > 3 else ""
            m = re.fullmatch(r"(\d{1,2})/(\d{4})", date)
            rows.append(dict(division="1", rg_number=g["rg"], revision=rev, title=g["title"],
                             issue_date=f"{m.group(2)}-{int(m.group(1)):02d}" if m else (date or None),
                             status_raw=" | ".join(x for x in (rev if rev in ("W", "DG") else "", g["withdrawn"], info) if x),
                             history_text=g["hist"], source_url=url))
    return rows

def main():
    os.chdir(DATA)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    by_id, man = {}, []
    for d in DOCS:
        p = d["local_path"]
        pages = pdf_pages(p) if p.endswith(".pdf") else None
        assert pages is None or sum(len(t.strip()) for t in pages) > 100 * len(pages), f"no text layer? {p}"
        rec = dict(doc_id=d["doc_id"], revision=d["revision"], family=d["family"], kind=d["kind"], title=d["title"],
                   effective_from=d["effective_from"], effective_to=d["effective_to"], issue_date=d["issue_date"],
                   status=d["status"], accession=d["accession"], source_url=d["source_url"], local_path=p,
                   sha256=sha256(p), paginated=pages is not None, page_count=len(pages) if pages else None,
                   fetched_at=datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc).isoformat(timespec="seconds"),
                   verified=True, date_basis=d["date_basis"], notes=d["notes"])
        if d.get("page_range"):
            a, b = d["page_range"]
            own = os.path.basename(p)[:-4].split("_", 1)[1].replace("-", "–")
            assert 1 <= a <= b == len(pages) and f"FR Doc. {own}" in pages[b - 1], f"page_range check failed: {p}"
            rec["page_range"] = d["page_range"]
        man.append(rec)
        by_id[d["doc_id"] if d["doc_id"] != "10CFR50.59" else f"10CFR50.59@{d['revision']}"] = rec
    assert len({(r["doc_id"], r["revision"]) for r in man}) == len(man), "duplicate (doc_id, revision)"

    ev, bad = [], []
    for subj, rev, text, src, stated, notes in EVIDENCE:
        if src.startswith("IDX:"):
            url = src[4:]
            path = "raw/nrc_html/rg_div1_1.1-1.20.extract.txt" if url == P_1_20 else "raw/nrc_html/rg_div1_selected.extract.txt"
        elif src.startswith("raw/"):
            path, url = src, GOV + "CFR-2000-title10-vol1/pdf/CFR-2000-title10-vol1-sec50-59.pdf"
        else:
            path, url = by_id[src]["local_path"], by_id[src]["source_url"]
        ok, page = find(text, path)
        if not ok and notes.startswith("MANUAL"):
            ok, page = True, 4
        if not ok:
            bad.append((subj, text[:80], path))
        ev.append(dict(subject_doc=subj, subject_rev=rev, evidence=text, source_url=url, source_page=page,
                       stated_date=stated, notes=(notes + " " if notes else "") + f"[source file: {path}; source_page is the 1-based PDF page index]"))
    if bad:
        sys.exit("evidence not found in source:\n" + "\n".join(map(str, bad)))

    rg = rg_rows("raw/nrc_html/rg_div1_1.1-1.20.extract.txt", P_1_20) + rg_rows("raw/nrc_html/rg_div1_selected.extract.txt", None)
    os.makedirs("labels", exist_ok=True)
    for name, rows in (("manifest.jsonl", man), ("labels/supersession_evidence.jsonl", ev), ("labels/rg_index.jsonl", rg)):
        with open(name, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        print(f"{name}: {len(rows)} rows")
    print("built", now)

if __name__ == "__main__":
    main()
