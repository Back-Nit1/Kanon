# Authenticated ADAMS verification — 2026-11-15 UTC

The official NRC APS Get Document endpoint returned HTTP 200 for all four accessions at 20:28:56–20:28:57 UTC. Sanitized JSON responses, including NRC indexed plain text and all record metadata, are in `aps_api/`. The locally saved [NRC API guide](https://adams-search.nrc.gov/assets/APS-API-Guide.pdf) specifies GET `/aps/api/search/{accessionNumber}` and subscription-key header authentication. The key is not included in these artifacts. The official `Url` field links to the PDF addresses below; direct GETs returned HTTP 403 for all four. No direct PDF bytes were obtained and no access controls were bypassed.

| Accession | Official document title | Document date | Types | Indexed characters | Estimated pages | NRC URL |
|---|---|---|---|---:|---:|---|
| ML22027A362 | James A. Fitzpatrick Nuclear Power Plant - Integrated Inspection Report 05000333/2021004 | 2022-01-27 | Inspection Report, Letter | 67,237 | 27 | [PDF](https://www.nrc.gov/docs/ML2202/ML22027A362.pdf) |
| ML15219A611 | IR 05000333/2015007; on 06/08 through 06/26/2015; James A. Fitzpatrick Nuclear Power Plant; Evaluation of Changes, Tests, or Experiments and Permanent Plant Modification Team Inspection Report. | 2015-08-06 | Inspection Report, Letter | 68,438 | 28 | [PDF](https://www.nrc.gov/docs/ML1521/ML15219A611.pdf) |
| ML12166A406 | IR 05000333-12-007; 4/16/2012 - 5/3/2012; James A. FitzPatrick Nuclear Power Plant (FitzPatrick); Engineering Specialist Plant Modifications Inspection. | 2012-06-14 | Inspection Report, Letter | 51,629 | 24 | [PDF](https://www.nrc.gov/docs/ML1216/ML12166A406.pdf) |
| ML083150382 | IR 05000333-08-004; on 07/01/08 to 09/30/08; James A. FitzPatrick Nuclear Power Plant; Refueling and Other Outage Activities; and Identification and Resolution of Problems. | 2008-11-07 | Inspection Report, Letter | 104,483 | 38 | [PDF](https://www.nrc.gov/docs/ML0831/ML083150382.pdf) |

The four existing Internet Archive PDFs were rehashed and page-counted locally at 2026-11-15T20:28:55Z; all hashes and counts match `candidates.jsonl`. For each, the first 500 characters of normalized API indexed text occur in normalized `pdftotext` output. This confirms a matching opening text, not complete text or byte identity. The API-reported page estimates match local counts. Comparison with direct NRC bytes remains blocked by 403; exact archive capture timestamps and independent CDX verification remain outstanding. Prior handoff claims about older captures and CDX are not independently verified here.

## Dataset verification

At about 2026-11-15 20:30 UTC, Hugging Face API metadata for [KL3M NRC](https://huggingface.co/api/datasets/alea-institute/kl3m-data-dotgov-www.nrc.gov) revision `a5654c7edcc64d81a8f1ae69410b681fc5917816` and [filtered KL3M NRC](https://huggingface.co/api/datasets/alea-institute/kl3m-filter-data-dotgov-www.nrc.gov) revision `71555fce5052b8c314939615d765836f6a09e628` reports publisher license CC BY 4.0 for each. Both have train splits, with publisher-stated repository storage of 884,392,726 and 623,211,555 bytes, respectively. The viewer's first rows use identifiers such as `s3://data.kl3m.ai/documents/dotgov/www.nrc.gov/docs/ML1735/ML17353A171.pdf.json`; the filter checks below use this exact path pattern for each accession. A zero result rules out that exact identifier in the indexed split, not alternative paths or full accession membership. Their text/tokens are derived materials, not authoritative PDF bytes. Earlier handoff reports anonymous S3 403, not independently retested here.


| Accession | KL3M exact-path result | Filtered KL3M exact-path result |
|---|---|---|
| ML22027A362 | Unknown: index loading / 502 on retry | 0 rows |
| ML15219A611 | 0 rows | 0 rows |
| ML12166A406 | 0 rows | 0 rows |
| ML083150382 | 0 rows | 0 rows |

Exact query URLs and identifier strings are in `candidates.jsonl`. A query returning HTTP 500 "dataset index is loading", 502 or timeout is unknown, not a negative. Successful zero responses are limited to the path spelling searched; none establishes absence from all possible identifiers.

The [Data.gov inspection reports catalog](https://catalog.data.gov/dataset/u-s-nuclear-power-plant-inspection-reports) lists an NRC inspection correspondence XLSX and states CC0 1.0; its metadata says last modified 2011-01-05. Its [NRC download](https://www.nrc.gov/reactors/operating/oversight/list-of-insp-rpts-dataset.xlsx) returned 403 during this pass, so membership of all four targets is unknown and its date alone does not prove exclusion of later reports. [NRC enforcement](https://huggingface.co/datasets/emperor-mew/nrc-enforcement) states CC0 for derived metadata, and the Hugging Face repository has no data files; all four targets remain unverified. [NRC regulatory embeddings](https://huggingface.co/datasets/davenporten/nrc-regulatory-embeddings) states MIT, and its card and [associated GitHub project](https://github.com/Davenporten/nrc-licensing-rag) describe CFR, guides and SRP text; membership of all four remains unverified. The GitHub project has no stated license visible in the repository root; it is a software project and does not establish a downloadable authoritative report corpus. No original target bytes were obtained from these candidates.

# ADAMS sourcing continuation — earlier pass (historical)

**Historical note:** The previous status below is superseded by the authenticated verification above. Earlier statements about a missing key and unconfirmed API records no longer apply.

The four requested FitzPatrick reports exist as Internet Archive captures in `wayback/`; see `candidates.jsonl` for full local SHA-256 values, original NRC addresses and page counts. PDF metadata and page counts were checked locally. The earlier handoff reports CDX SHA-1 matching and matching text in older captures, but the exact capture timestamps and CDX responses were not preserved; treat independent archive-byte verification as outstanding. These files are staged candidates, not direct NRC downloads or approved corpus records.

The NRC describes [ADAMS Public Search](https://www.nrc.gov/documents-reports/adams-public-documents) as the public interface to its official records. No usable `ADAMS_API_KEY` was present in the process environment or the local `.env` during this check. The four existing `aps_api/*.json` responses report 401 missing subscription key. No new authenticated request was made. Configure a replacement key in the ignored `.env` or process environment, then query the official APS API and compare returned identity and text to these captures. PDF downloads can still be separately blocked.

## Dataset survey

| Candidate | License as stated by publisher | Assessment |
|---|---|---|
| [KL3M NRC crawl](https://huggingface.co/datasets/alea-institute/kl3m-data-dotgov-www.nrc.gov) | CC BY 4.0 per earlier metadata review | NRC site crawl, potentially useful for ADAMS text. Membership for the four accessions remains unverified; earlier S3 document reads returned 403. Current web fetch failed, so size, license and membership were not independently reconfirmed. |
| [KL3M filtered NRC crawl](https://huggingface.co/datasets/alea-institute/kl3m-filter-data-dotgov-www.nrc.gov) | CC BY 4.0 per earlier metadata review | Filtered variant; membership and accessibility remain unverified. |
| [NRC enforcement](https://huggingface.co/datasets/emperor-mew/nrc-enforcement) | CC0 1.0 for derived data, publisher claim | Hugging Face card contains no dataset files and points to a live API for enforcement action metadata and links; not evidence that the four inspection reports are included. |
| [NRC regulatory embeddings](https://huggingface.co/datasets/davenporten/nrc-regulatory-embeddings) | MIT, publisher claim | 37,734 text chunks and vectors from SRP, CFR and regulatory guides; useful as a discovery lead for guides, not an original PDF corpus or established source for these four inspection reports. |

[NRC data catalog](https://www.nrc.gov/documents-reports/nrc-data) lists other datasets and references to ADAMS, but a bulk dataset containing the four reports was not identified in this pass. Earlier OSTI searches found no hits. Kaggle and GitHub coverage and licenses have not been established; absence of evidence here is not proof the documents are absent. The [GitHub project associated with the embeddings](https://github.com/Davenporten/nrc-licensing-rag) is a research lead, not a verified original document source.

Next: obtain a working key securely, confirm the four APS records and their source URLs, recover capture timestamps/CDX digests, and independently verify report text and dates before adding anything to the production manifest. Complete KL3M accession membership and remaining dataset searches when their endpoints are accessible.
