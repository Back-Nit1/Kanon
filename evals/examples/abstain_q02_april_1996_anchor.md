# Kanon trace — ABSTAIN

**Question:** In April 1996 a research reactor licensee installed a new primary cooling pump and motor and removed the pump discharge check valve internals. What did 10 CFR 50.59 require for that change at the time, and did it need prior NRC approval?

- Mode: `graph`
- Query kind: `time_anchored`
- Authority date (as_of): `1996-04-01`

## 1. Tool calls

### `search_current_requirements`  args: `{"query": "10 CFR 50.59 requirements governing changes to a facility as described in the safety analysis report in April 1996, including written safety evaluation, unreviewed safety question criteria, and when prior Commission approval was required for installation of a replacement primary cooling pump and motor and removal of pump discharge check valve internals at a research reactor", "as_of": "1996-04-01", "plant": null, "unit": null, "docket": null}`
```json
[]
```

### `get_revision_history`  args: `{"doc_id": "10CFR50.59"}`
```json
[
  {
    "doc_id": "10CFR50.59",
    "revision": "2025-01-29",
    "family": "10CFR50.59",
    "kind": "regulation",
    "title": "10 CFR 50.59 Changes, tests, and experiments (eCFR point-in-time 2025-01-29, current)",
    "effective_from": "2025-01-29",
    "effective_to": null,
    "issue_date": null,
    "status": "active",
    "accession": null,
    "source_url": "https://www.ecfr.gov/api/versioner/v1/full/2025-01-29/title-10.xml?part=50&section=50.59",
    "plants": [],
    "units": [],
    "dockets": [],
    "applicability_scope": "global"
  },
  {
    "doc_id": "10CFR50.59",
    "revision": "2024-08-15",
    "family": "10CFR50.59",
    "kind": "regulation",
    "title": "10 CFR 50.59 Changes, tests, and experiments (eCFR point-in-time 2024-08-15)",
    "effective_from": "2024-08-15",
    "effective_to": "2025-01-29",
    "issue_date": null,
    "status": "superseded",
    "accession": null,
    "source_url": "https://www.ecfr.gov/api/versioner/v1/full/2024-08-15/title-10.xml?part=50&section=50.59",
    "plants": [],
    "units": [],
    "dockets": [],
    "applicability_scope": "global"
  },
  {
    "doc_id": "10CFR50.59",
    "revision": "2007-09-27",
    "family": "10CFR50.59",
    "kind": "regulation",
    "title": "10 CFR 50.59 Changes, tests, and experiments (eCFR point-in-time 2017-01-01)",
    "effective_from": "2007-09-27",
    "effective_to": "2024-08-15",
    "issue_date": null,
    "status": "superseded",
    "accession": null,
    "source_url": "https://www.ecfr.gov/api/versioner/v1/full/2017-01-01/title-10.xml?part=50&section=50.59",
    "plants": [],
    "units": [],
    "dockets": [],
    "applicability_scope": "global"
  },
  {
    "doc_id": "10CFR50.59",
    "revision": "2001-12-14",
    "family": "10CFR50.59",
    "kind": "regulation",
    "title": "10 CFR 50.59 Changes, tests, and experiments (CFR edition revised as of 1-1-02)",
    "effective_from": "2001-12-14",
    "effective_to": "2007-09-27",
    "issue_date": null,
    "status": "sup
```

### `find_precedent_events`  args: `{"system": "research reactor primary cooling system pump, motor, and pump discharge check valve", "symptom": "installed a new primary cooling pump and motor and removed the pump discharge check valve internals without an adequate 10 CFR 50.59 safety evaluation or prior NRC approval", "since": "1996-04-01", "plant": null, "unit": null, "docket": null}`
- `EA-98-155@0#0002` — EA-98-155 Rev 0, p.1, status=active, §EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)
- `EA-98-155@0#0007` — EA-98-155 Rev 0, p.1, status=active, §EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)
- `IR-05000333-2021004@0#0030` — IR-05000333-2021004 Rev 0, p.11, status=active, §INSPECTION RESULTS
- `EA-98-155@0#0003` — EA-98-155 Rev 0, p.1, status=active, §EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)
- `IR-05000333-2015007@0#0038` — IR-05000333-2015007 Rev 0, p.12, status=active, §a. Inspection Scope
- `EA-98-155@0#0006` — EA-98-155 Rev 0, p.1, status=active, §EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)
- `IR-05000333-2021004@0#0008` — IR-05000333-2021004 Rev 0, p.5, status=active, §List of Findings and Violations
- `IR-05000333-2021004@0#0035` — IR-05000333-2021004 Rev 0, p.12, status=active, §INSPECTION RESULTS

## 2. Authority resolutions (deterministic lookup)


## 3. Gate decision (Tier B, deterministic)

- Decision: **abstain** (enforcing: True)
- violation `no_governing_requirement`: — Rev — — no requirement document in the store governed on 1996-04-01 for this question; the record does not reach back that far
- violation `no_requirement_citation`: — Rev — — the question asks for governing requirements but the answer cites only records

## 4. Citations

- EA-98-155 Rev 0, p.1, §EA-98-155 Notice of Violation, University of Michigan Ford Nuclear Reactor (primary coolant pump replacement, inadequate 10 CFR 50.59 evaluation)

## 5. Output

ABSTAINED — authority could not be verified:
- [no_governing_requirement] no requirement document in the store governed on 1996-04-01 for this question; the record does not reach back that far
- [no_requirement_citation] the question asks for governing requirements but the answer cites only records
