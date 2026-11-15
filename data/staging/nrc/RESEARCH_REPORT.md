# NRC source research continuation

Research continuation, 2026-11-15. The candidate index accompanies this report. Files are staging candidates, not additions to the authority corpus. SHA-256 confirms local bytes, not that an archived copy is the complete or authoritative original.

## Recovered documents

- RG 1.187 revisions 0–3 and DG-1356 were retrieved from Internet Archive captures of NRC URLs. Revision 0's first page identifies RG 1.187 and November 2000 despite its unhelpful PDF title. The three revision issuance notices in `data/raw/fr/` remain the evidence for availability/effective dates; issue date and effective date must not be conflated.
- Safety Guide 1 and RG 1.82 revisions 0–4 are staged; their PDF metadata identifies their guide/revision. Their capture timestamps were not preserved in the available handoff; exact archive provenance still needs CDX verification. The `.partial` revision 5 download must not be ingested.
- 25 archived index HTML pages exist in `rg_index/`, covering the division landing pages and Division 1 ranges. Exact capture URLs and completeness must be independently checked before extraction into labels.
- FitzPatrick Amendment 309 (ML15014A277), dated February 9, 2015, contains explicit removal/insertion instructions for license page 3 and technical specifications page 2.0-1. It modifies the low pressure safety limit, not a pump-specific amendment. [NRC ADAMS indexed record](https://adamswebsearch2.nrc.gov/webSearch2/main.jsp?AccessionNumber=ML15014A277) confirms its identity. The staged PDF's exact archival capture URL remains to be reconstructed.
- Point Beach inspection report 50-266/02-15 and 50-301/02-15 is staged. The [NRC inspection report](https://www.nrc.gov/reactors/operating/oversight/reports/poin_2002015.pdf) and [EA-03-057 notice](https://www.nrc.gov/reading-rm/doc-collections/enforcement/actions/reactors/ea03057) substantiate the AFW orifice modifications and plugging concern. The notice describes modification packages 99-029 A–D and 02-029. Capture route for the local PDF remains unverified.
- [NRC Part 21 report 1996-311](https://www.nrc.gov/regulations-legislation/reports-associated-with-events/part-21-reports/1996/1996311) contains FitzPatrick LER 96-002-00 about residual heat removal service water pump circuit breakers; NRC's [LER listing](https://www.nrc.gov/regulations-legislation/reports-associated-with-events/part-21-reports/ler-that-may-report-defects-and-noncompliance) identifies 2024 LER ML24327A097. These are source leads, not locally downloaded LER PDFs.
- DG-1466 metadata is present; its PDF download returned 403. The staged GovInfo proposed rule is not the guide itself.

## Remaining work

Locate older RG 1.84/1.85 PDFs and remaining pump guides; establish exact archive captures and compare hashes against CDX records; extract and verify index rows; obtain more FitzPatrick amendments and LERs; and independently verify all staged artifacts. For 57 FR 39358, the [2016 NRC notice](https://www.govinfo.gov/content/pkg/FR-2016-08-30/pdf/2016-20804.pdf) identifies the 1992 rule and October 1, 1992 effective date, but does not establish the requested 50.59 text. A separate per-document PDF was not established; the previously identified whole-issue scan remains the fallback. Do not infer that no separate PDF exists.

Dataset survey remains incomplete: KL3M's NRC crawl is a lead, not proof of coverage for these target documents; its license and access should be checked per dataset before use. Kaggle, GitHub and data.gov coverage remains unverified. No active ADAMS key was observed by the prior researcher; check local secret configuration without recording its value.
