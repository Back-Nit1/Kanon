# Kanon Temporal Differencing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Name the exact CFR paragraph that governed on one date and does not govern on another, explain every gate block in those terms, and reject citations to paragraphs that do not exist in the cited revision.

**Architecture:** One new deterministic module (`kanon/diff.py`) extracts paragraph designators from the text of chunks already indexed in Chroma, scoped to a single CFR section. Two one-method reads are added to existing layers (`CorpusIndex.chunks_for`, `AuthorityStore.edges_out_of`) because every current read path is a ranked search or an edge lookup in the wrong direction. The diff is then surfaced three ways: a CLI command, an agent tool with a hard wall-clock timeout, and inside the Tier B gate's abstain message. No LLM anywhere in the diff or the gate.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite (`kanon/store.py`), Chroma (`kanon/retrieval.py`), LangChain tools / LangGraph (`kanon/tools.py`, `kanon/agent.py`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-15-kanon-temporal-diff-design.md`

## Global Constraints

- No LLM in the diff path or in any authority decision. Only deterministic lookups in `kanon/store.py` decide authority. The model classifies, selects tools, drafts, and lists what it cited — nothing else.
- No re-ingest and no re-embedding: `CHUNKING_VERSION` stays at `3`. The diff reads chunks already in Chroma.
- No new dependency. Standard library plus what `pyproject.toml` already pins.
- `as_of`-style date parameters never get a default. `diff_revisions` takes `as_of_from` and `as_of_to`, both required.
- Diff tool wall-clock budget: **10 seconds**, as `DIFF_TIMEOUT_S = 10`. On expiry return a typed result, never raise into the LangGraph run.
- `evals/questions.yaml` is pinned and must not be edited by this work. No new eval question type.
- Only 10 CFR 50.59 has text across revisions. Any other `doc_id` must report that no text is indexed rather than return an empty diff.
- Tests are pure unit tests: in-memory `AuthorityStore()` and hand-built `Chunk` objects or a duck-typed fake index. No Chroma server, no Postgres, no network. Follow `tests/test_store.py`: real verbatim NRC strings as module-level constants.
- `kanon/diff.py` holds no mutable module state and performs no I/O of its own; it receives the index and store.
- The diff reports exactly what `resolve_authority` says governed; it never second-guesses the resolver. A known resolver gap found by audit on 2026-11-15 — `AuthorityStore.governs` (`kanon/store.py:246-258`) ignores the `documents.status` column, so a `withdrawn` row with no `effective_to` and no dated ending edge resolves as governing (10 such rows in `authority.db`, all metadata-only reg guides) — is **out of scope for this plan** and must not be fixed inside it. Do not add a `status` check to `governs` here; it changes the eval baselines.

## Review Focus

1. **A date before the earliest recorded revision** (anything before 1996-08-28 for 10 CFR 50.59) — must report that nothing governed on that side, not an empty diff that reads as "nothing changed". Test in Task 3.
2. **A metadata-only document** (`RG-1.187`, every reg guide in this corpus) — must report that no text is indexed for the resolved revision. Test in Task 3.
3. **A conflict-window date** (RG 1.85 between 2003-06 and 2004-06-18, where the resolver returns `unresolved`) — must surface the unresolved edges instead of diffing an arbitrary revision. Test in Task 3.
4. **Free-text `Citation.section` from the model** (`§ 50.59(a)(2)`, `50.59(a)(2)`, `(a)(2)`, `a.2`, `Changes, tests, and experiments`) — designator parsing must fail closed to "no designator found", so an unparseable section never becomes a violation. Test in Task 4.
5. **`as_of_from == as_of_to`, and both dates resolving to the same revision** — an empty diff with both revisions named, no crash and no spurious `changed` entries from text normalization. Test in Task 3.

---

## File Structure

- **Create `kanon/diff.py`** — paragraph extraction, paragraph diffing, date-to-date diffing, output formatting, and `designator_from_section`. Deterministic, no model, no I/O of its own.
- **Create `tests/test_diff.py`** — extraction, diffing, and the Review Focus cases above.
- **Modify `kanon/retrieval.py`** — add `CorpusIndex.chunks_for`.
- **Modify `kanon/store.py`** — add `AuthorityStore.edges_out_of`.
- **Modify `kanon/agent.py`** — `paragraph_not_in_revision` rule in `check_citations`, `citation_notes`, diff lines in `abstain`.
- **Modify `kanon/tools.py`** — `diff_revisions` tool with the 10 s budget.
- **Modify `kanon/cli.py`** — `kanon diff` subcommand.
- **Modify `tests/test_agent.py`, `tests/test_store.py`** — gate rule and `edges_out_of` tests, in the existing files.
- **Modify `README.md`** — layer table row and the `## Run` block.

---

### Task 1: Paragraph extraction from chunk text

**Files:**
- Create: `kanon/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `kanon.ingest.Chunk` (fields `text`, `page`, `doc_id`, `revision`, `id`).
- Produces:
  - `class Paragraph(BaseModel)`: `key: str`, `text: str`, `page: int`, `doc_id: str`, `revision: str`
  - `section_region(text: str, section: str = "50.59") -> str`
  - `paragraphs(chunks: list[Chunk], section: str = "50.59") -> dict[str, Paragraph]`
  - `normalize(text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Use these verbatim strings as module constants — they were read from the Chroma index for `10CFR50.59@1996-08-28` and `10CFR50.59@2025-01-29`:

```python
R1996 = (
    "[27 FR 12186, Dec. 8, 1962, as amended at 35 FR 11461, July 17, 1970]\n"
    "§50.59 Changes, tests and experiments.\n"
    "(a)(1) The holder of a license authorizing operation of a production or utilization "
    "facility may (i) make changes in the facility as described in the safety analysis report, "
    "(ii) make changes in the procedures as described in the safety analysis report, and "
    "(iii) conduct tests or experiments not described in the safety analysis report.\n"
    "(2) A proposed change, test, or experiment shall be deemed to involve an unreviewed safety "
    "question (i) if the probability of occurrence or the consequences of an accident or "
    "malfunction of equipment important to safety previously evaluated in the safety analysis "
    "report may be increased.\n"
    "§50.60 Acceptance criteria for fracture prevention measures for lightwater nuclear "
    "power reactors for normal operation.\n"
    "(a) Except as provided in paragraph (b) of this section, all light-water nuclear power "
    "reactors must meet the fracture toughness requirements.\n")
```

```python
def test_section_region_drops_neighbouring_sections():
    # the 1996 CFR-edition chunk carries 50.58/50.60/50.61 text under a 50.59 doc_id
    region = section_region(R1996)
    assert "unreviewed safety question" in region
    assert "fracture toughness" not in region
    assert "27 FR 12186" not in region

def test_section_region_empty_when_section_absent():
    assert section_region("(a) Some unrelated text with no section heading.") == ""

def test_paragraph_keys_and_hierarchy():
    out = paragraphs([chunk(R1996, page=1)])
    assert set(out) == {"(a)(1)", "(a)(2)"}          # bare "(2)" inherits parent "(a)"
    assert out["(a)(2)"].text.startswith("A proposed change")
    assert out["(a)(2)"].page == 1
    assert "(i) if the probability" in out["(a)(2)"].text   # inline romans stay in the body

def test_paragraph_spanning_two_chunks_is_joined():
    first = chunk("§ 50.59 Changes, tests, and experiments.\n(c)(1) A licensee may make "
                  "changes in the facility", page=1)
    second = chunk("§ 50.59 Changes, tests, and experiments.\nwithout obtaining a license "
                   "amendment pursuant to § 50.90 only if:", page=2)
    out = paragraphs([first, second])
    assert "without obtaining a license amendment" in out["(c)(1)"].text
    assert out["(c)(1)"].page == 1                   # the page where the paragraph starts

def test_normalize_ignores_whitespace_and_quote_style():
    assert normalize("the  ''Code'',\nas  updated") == normalize("the “Code”, as updated")
```

`chunk(text, page)` is a local helper in the test file building a `Chunk` with the required metadata fields filled with placeholders.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kanon.diff'`

- [ ] **Step 3: Implement `kanon/diff.py` extraction**

`section_region(text, section)`: find each `§`-style heading for a CFR section number (match `§` optionally spaced, then `\d+\.\d+[a-z]?`); keep the span that starts at a heading whose number equals `section` and ends at the next heading with a different number, or end of text. No 50.59 heading means return `""`. Multiple 50.59 heading occurrences (the contextualized chunk prefix repeats the heading) concatenate in order.

`paragraphs(chunks, section)`: iterate chunks sorted by `(page, id)`; for each, walk `section_region` and split on designators. Classify each designator by shape to get its depth: digits → depth 1, lowercase roman (`i`, `ii`, `iv`, `v`, `ix`, `x`...) → depth 2 **only when the running path already has depth ≥ 2**, uppercase letters → depth 3, otherwise lowercase letter → depth 0. Maintain the running path, truncating it to the new depth, and build the key as the concatenation of its parts (`"(a)(2)"`). Append body text to the current key; when a key recurs across chunks, append to the existing `Paragraph` and keep the first `page`.

Only a designator that opens a line (allowing a leading Docling `- ` bullet) starts a new paragraph; a designator inside a sentence stays in the body — that is what keeps `(i) if the probability` inside `(a)(2)`.

```python
# ponytail: shape-based depth, so a renumbered-but-identical paragraph reads as removed + added,
# and "(i)" at depth 0 is read as the letter i. Upgrade path: per-paragraph chunking
# (CHUNKING_VERSION bump) if either case shows up in a real corpus.
```

`normalize(text)`: casefold, collapse all whitespace runs to one space, map `''`/`` `` ``/`“”‘’` to `"`, strip. Used only for `changed` comparison, never for display.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add kanon/diff.py tests/test_diff.py
git commit -m "feat(diff): extract CFR paragraph designators from indexed chunk text"
```

---

### Task 2: The two missing reads

**Files:**
- Modify: `kanon/retrieval.py` (new method on `CorpusIndex`, in the `# ---- reads` section)
- Modify: `kanon/store.py` (new method on `AuthorityStore`, next to `edges_into`)
- Test: `tests/test_retrieval_units.py`, `tests/test_store.py`

**Interfaces:**
- Produces:
  - `CorpusIndex.chunks_for(doc_key: str) -> list[Chunk]` — every chunk of one revision from hot **and** cold, sorted by `(page, id)`. Unknown `doc_key` returns `[]`.
  - `AuthorityStore.edges_out_of(doc_id: str, revision: str, known_at: datetime | None = None) -> list[Edge]` — edges whose **source** is `(doc_id, revision)`: what this revision superseded. Respects the record-time `known_at` filter exactly as `edges_into` does.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py — extends the existing 50.59 fixture
def test_edges_out_of_returns_what_a_revision_superseded(store):
    out = store.edges_out_of("10CFR50.59", "2001-03-13")
    assert [(e.to_doc, e.to_rev) for e in out] == [("10CFR50.59", "1996-08-28")]
    assert store.edges_out_of("10CFR50.59", "1996-08-28") == []

def test_edges_out_of_respects_known_at(store):
    before = datetime(1990, 1, 1, tzinfo=timezone.utc)
    assert store.edges_out_of("10CFR50.59", "2001-03-13", known_at=before) == []
```

```python
# tests/test_retrieval_units.py — fake Chroma collection returning two chunks out of page order
def test_chunks_for_merges_tiers_in_page_order():
    index = fake_index(hot=[c_page2], cold=[c_page1])
    assert [c.page for c in index.chunks_for("10CFR50.59@1996-08-28")] == [1, 2]

def test_chunks_for_unknown_doc_key_is_empty():
    assert fake_index(hot=[], cold=[]).chunks_for("RG-1.187@2") == []
```

Follow whatever fake/stub pattern `tests/test_retrieval_units.py` already uses for collections; if it has none, a minimal object with `.count()` and `.get(where=..., include=...)` is enough.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_retrieval_units.py -v`
Expected: FAIL with `AttributeError: 'AuthorityStore' object has no attribute 'edges_out_of'` and the same for `chunks_for`.

- [ ] **Step 3: Implement both methods**

`edges_out_of` mirrors `edges_into`: same `SELECT`, `WHERE from_doc=? AND from_rev=?`, same `_live(row, known_at)` filter, same `_edge` mapping. Add `CREATE INDEX IF NOT EXISTS edges_from ON supersession_edges (from_doc, from_rev);` to `SCHEMA` — `IF NOT EXISTS` means existing `authority.db` files pick it up on open, no migration.

`chunks_for` calls `col.get(where={"doc_key": doc_key}, include=["documents", "metadatas"])` on `self.hot` and `self.cold`, builds `Chunk.from_chroma(...)` for each row, and returns them sorted by `(page, id)`. No embedding call, no BM25, no reranker — this is a metadata read, not a search.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_retrieval_units.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kanon/store.py kanon/retrieval.py tests/test_store.py tests/test_retrieval_units.py
git commit -m "feat(store,retrieval): edges_out_of and chunks_for reads for revision diffing"
```

---

### Task 3: Date-to-date diff and the `kanon diff` command

**Files:**
- Modify: `kanon/diff.py`
- Modify: `kanon/cli.py` (subparser block around `kanon/cli.py:30-37`, handler after the `resolve` branch)
- Modify: `README.md`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: Task 1's `paragraphs`/`normalize`; Task 2's `chunks_for` and `edges_out_of`; `AuthorityStore.resolve_authority`, `revision_history`, `edges_into`, `get`.
- Produces:
  - `class ParagraphDelta(BaseModel)`: `key: str`, `old: Paragraph`, `new: Paragraph`
  - `class DiffResult(BaseModel)`: `doc_id: str`, `from_as_of: date`, `to_as_of: date`, `from_revisions: list[str]`, `to_revisions: list[str]`, `gone: list[Paragraph]`, `added: list[Paragraph]`, `changed: list[ParagraphDelta]`, `ended_by: list[Edge]`, `unresolved: list[Edge]`, `notes: list[str]`
  - `diff_paragraphs(old: dict[str, Paragraph], new: dict[str, Paragraph]) -> tuple[list[Paragraph], list[Paragraph], list[ParagraphDelta]]`
  - `diff_as_of(index, store, doc_id: str, from_as_of: date, to_as_of: date, section: str | None = None) -> DiffResult`
  - `format_diff(result: DiffResult, quote_chars: int = 160) -> str`

`section` defaults to the numeric part of `doc_id` when it looks like `10CFR<section>` (so `10CFR50.59` → `50.59`), otherwise extraction is skipped and a note is recorded.

- [ ] **Step 1: Write the failing tests**

```python
def test_diff_paragraphs_classifies_gone_added_changed():
    old = {"(a)(2)": p("(a)(2)", "unreviewed safety question"), "(c)": p("(c)", "who desires")}
    new = {"(a)(1)": p("(a)(1)", "Change means a modification"), "(c)": p("(c)", "only if:")}
    gone, added, changed = diff_paragraphs(old, new)
    assert [g.key for g in gone] == ["(a)(2)"]
    assert [a.key for a in added] == ["(a)(1)"]
    assert [d.key for d in changed] == ["(c)"]

def test_identical_text_with_different_whitespace_is_not_changed():
    old = {"(a)": p("(a)", "the  ''Code'',\nas updated")}
    new = {"(a)": p("(a)", "the “Code”, as updated")}
    assert diff_paragraphs(old, new)[2] == []

def test_diff_as_of_names_both_governing_revisions(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(2000, 6, 1), date(2026, 11, 15))
    assert r.from_revisions == ["1996-08-28"] and r.to_revisions == ["2025-01-29"]
    assert "(a)(2)" in [g.key for g in r.gone]
    assert [(e.from_doc, e.from_rev) for e in r.ended_by] == [("10CFR50.59", "2001-03-13")]

def test_same_date_both_sides_is_an_empty_diff(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(2000, 6, 1), date(2000, 6, 1))
    assert (r.gone, r.added, r.changed) == ([], [], [])
    assert r.from_revisions == r.to_revisions == ["1996-08-28"]

def test_date_before_earliest_revision_says_nothing_governed(index, store):
    r = diff_as_of(index, store, "10CFR50.59", date(1996, 4, 1), date(2026, 11, 15))
    assert r.from_revisions == []
    assert any("nothing governed on 1996-04-01" in n for n in r.notes)
    assert r.gone == []                       # not "nothing changed"

def test_metadata_only_document_reports_no_text(index, store):
    r = diff_as_of(index, store, "RG-1.187", date(2019, 12, 1), date(2020, 8, 1))
    assert any("no text indexed" in n for n in r.notes)
    assert (r.gone, r.added, r.changed) == ([], [], [])

def test_conflict_window_surfaces_unresolved_instead_of_diffing(index, store):
    r = diff_as_of(index, store, "RG-1.85", date(2003, 9, 1), date(2026, 11, 15))
    assert r.unresolved
    assert any("authority is unresolved" in n for n in r.notes)
    assert (r.gone, r.added, r.changed) == ([], [], [])

def test_format_diff_labels_every_line_with_revision_and_page():
    text = format_diff(a_result_with_one_gone_paragraph())
    assert "GONE" in text and "(a)(2)" in text and "1996-08-28" in text and "p.1" in text
```

The `index`/`store` fixtures are local: an in-memory `AuthorityStore` carrying the 50.59 timeline (1996-08-28, 2001-03-13, 2025-01-29), the RG-1.187 metadata rows, and the RG-1.85 conflict-window edges from `tests/test_supersession.py`; plus a fake index whose `chunks_for` returns the Task 1 constants for the 50.59 revisions and `[]` for everything else.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL, `ImportError: cannot import name 'diff_as_of'`

- [ ] **Step 3: Implement `diff_paragraphs`, `diff_as_of`, `format_diff`**

`diff_as_of` order of operations, which the tests above pin:
1. `store.resolve_authority(doc_id, as_of)` for each side. Record `authoritative` keys as `from_revisions` / `to_revisions`.
2. If either side has `unresolved` edges, record them plus a note containing `authority is unresolved`, and return with empty gone/added/changed. An ambiguous record is reported, never diffed.
3. For an empty `authoritative` side, note `nothing governed on <date>` (and, as `kanon resolve` already does in `kanon/cli.py:53-58`, the earliest recorded version) and return without diffing.
4. Load each side's chunks via `index.chunks_for(f"{doc}@{rev}")` for every authoritative revision. If a side has zero chunks, note `no text indexed for <doc>@<rev>` and return.
5. Extract with `paragraphs`, diff with `diff_paragraphs`.
6. `ended_by`: `store.edges_into(doc, rev)` for each `from_revisions` entry, keeping dated non-`partial` edges whose `valid_from` falls in `(from_as_of, to_as_of]`.

`format_diff` prints a header naming each side's date, revision, and whether it is a requirement or a record (`kanon.retrieval.is_record` on the stored `kind`), then one block per `GONE` / `NEW` / `CHANGED` entry with key, truncated quote, `doc@rev`, `p.<page>`, and for `GONE` the ending edge's `describe()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v`
Expected: PASS

- [ ] **Step 5: Add the CLI subcommand**

In `kanon/cli.py`: a `diff` subparser with positional `doc_id`, required `--from` and `--to` as `date.fromisoformat` (store them as `from_as_of` / `to_as_of` via `dest=`, since `from` is a keyword), and an optional `--section`. Handle it in the branch that already has `index` available (after the `CorpusIndex` construction, alongside `ask`), printing `format_diff(...)`. Update the module docstring's command list and the `## Run` block plus the layer table in `README.md`.

- [ ] **Step 6: Verify the command end to end**

Run: `cd /Users/nityanthmaramreddy/gord-demo && uv run kanon diff 10CFR50.59 --from 2000-06-01 --to 2026-11-15`
Expected: a header naming `10CFR50.59@1996-08-28` and `10CFR50.59@2025-01-29`, at least one `GONE` block, and every line carrying a revision and a page. Also run `uv run kanon diff RG-1.187 --from 2019-12-01 --to 2020-08-01` and expect the `no text indexed` note, not a traceback.

- [ ] **Step 7: Commit**

```bash
git add kanon/diff.py kanon/cli.py tests/test_diff.py README.md
git commit -m "feat(diff): point-in-time paragraph diff and kanon diff command"
```

---

### Task 4: `paragraph_not_in_revision` gate rule

**Files:**
- Modify: `kanon/diff.py` (add `designator_from_section`)
- Modify: `kanon/agent.py` (`check_citations` at `kanon/agent.py:166`, its call site in the `validate` node, and `AUTHORITY_RULES` left untouched)
- Test: `tests/test_diff.py`, `tests/test_agent.py`

**Interfaces:**
- Produces:
  - `designator_from_section(section: str | None) -> str | None` in `kanon/diff.py` — the paragraph key inside a model-written section label, or `None` when none parses.
  - `check_citations(store, citations, retrieved, as_of, query_kind, *, paragraphs: Callable[[str, str], dict[str, Paragraph]] | None = None) -> list[Violation]` — today's five positional parameters unchanged, plus one **keyword-only** resolver. `None` skips the new rule, so every existing call and test keeps working.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diff.py
def test_designator_from_section_handles_model_phrasings():
    assert designator_from_section("§ 50.59(a)(2)") == "(a)(2)"
    assert designator_from_section("50.59(a)(2)") == "(a)(2)"
    assert designator_from_section("(a)(2)") == "(a)(2)"
    assert designator_from_section("50.59(c)(2)(i)") == "(c)(2)(i)"

def test_designator_from_section_fails_closed():
    for bad in [None, "", "a.2", "Changes, tests, and experiments", "§ 50.59", "50.59"]:
        assert designator_from_section(bad) is None
```

```python
# tests/test_agent.py
def test_paragraph_not_in_revision_blocks_an_invented_paragraph():
    rules = [v.rule for v in check_citations(store, [cit(section="§ 50.59(z)(9)")], retrieved,
                                             AS_OF, "time_anchored", paragraphs=resolver)]
    assert "paragraph_not_in_revision" in rules

def test_paragraph_present_in_revision_is_clean():
    rules = [v.rule for v in check_citations(store, [cit(section="§ 50.59(a)(2)")], retrieved,
                                             AS_OF, "time_anchored", paragraphs=resolver)]
    assert "paragraph_not_in_revision" not in rules

def test_unparseable_or_missing_section_is_not_a_violation():
    for section in (None, "Changes, tests, and experiments"):
        rules = [v.rule for v in check_citations(store, [cit(section=section)], retrieved, AS_OF,
                                                 "time_anchored", paragraphs=resolver)]
        assert "paragraph_not_in_revision" not in rules

def test_paragraph_rule_is_an_integrity_rule_not_an_authority_rule():
    from kanon.agent import AUTHORITY_RULES
    assert "paragraph_not_in_revision" not in AUTHORITY_RULES   # therefore blocks in every mode
```

`resolver` is `lambda doc_id, revision: {"(a)(1)": ..., "(a)(2)": ...}`; `cit(...)` builds a `Citation` whose `(doc_id, revision, page)` is in `retrieved` and whose document is in the in-memory store, so no other rule fires.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/test_diff.py -v`
Expected: FAIL, `TypeError: check_citations() got an unexpected keyword argument 'paragraphs'`

- [ ] **Step 3: Implement the rule**

`designator_from_section`: strip a leading `§` and any `\d+\.\d+` section number, then require the remainder to be one or more `(...)` groups whose contents match the designator shapes from Task 1. Anything else returns `None`. Never guess: `a.2` has no parentheses, so it is `None`.

In `check_citations`, after the existing per-citation checks and only when `paragraphs is not None` and `designator_from_section(cit.section)` is not `None`: look up `paragraphs(cit.doc_id, cit.revision)`; if the designator is absent, append `Violation(rule="paragraph_not_in_revision", ...)` whose detail names the designator, the revision, and up to ~8 keys that revision does have. Skip records (`is_record(doc.kind)`), consistent with the governing check above it.

Wire the `validate` node to pass `paragraphs=lambda d, r: kanon.diff.paragraphs(index.chunks_for(f"{d}@{r}"))`, memoized per run in a dict so a multi-citation answer reads each revision once.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent.py tests/test_diff.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kanon/agent.py kanon/diff.py tests/test_agent.py tests/test_diff.py
git commit -m "feat(gate): reject citations to paragraphs absent from the cited revision"
```

---

### Task 5: Citation provenance and the explained block

**Files:**
- Modify: `kanon/agent.py` (`answer` node at `kanon/agent.py:320-324`, `abstain` node below it)
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: Task 2's `edges_out_of`; Task 3's `diff_as_of` and `format_diff`.
- Produces:
  - `citation_notes(store: AuthorityStore, citations: list[Citation], as_of: date) -> list[str]` in `kanon/agent.py` — one line per citation: `doc_id Rev revision, p.page[, §section]` then `requirement` or `record`, `governed <effective_from>..<effective_to|open>`, `checked against as_of=<date>`, and `supersedes: ...` from `edges_out_of` (omitted when it supersedes nothing).

- [ ] **Step 1: Write the failing tests**

```python
def test_citation_notes_carry_the_three_developer_facts(store):
    line = citation_notes(store, [Citation(doc_id="10CFR50.59", revision="2001-03-13", page=1)],
                          date(2001, 4, 1))[0]
    assert "requirement" in line
    assert "as_of=2001-04-01" in line
    assert "supersedes: 10CFR50.59 Rev 1996-08-28" in line

def test_record_citation_is_labelled_a_record_not_a_requirement(store):
    line = citation_notes(store, [Citation(doc_id="EA-98-155", revision="0", page=1)],
                          date(2001, 4, 1))[0]
    assert "record" in line and "requirement" not in line

def test_abstain_message_names_what_governs_on_the_asked_date():
    out = abstain_text(violations=[Violation(rule="not_governing_on_as_of", doc_id="10CFR50.59",
                                             revision="2025-01-29", detail="did not govern")],
                       as_of=date(2001, 4, 1))
    assert "10CFR50.59@2001-03-13" in out      # what IS governing, not only what was wrong
    assert "GONE" in out or "CHANGED" in out   # the diff lines proving the difference
```

`abstain_text` is a thin local helper in the test that runs the `abstain` node function with a minimal state; keep it next to the existing `_needs_requirements` tests in the same file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -v`
Expected: FAIL, `ImportError: cannot import name 'citation_notes'`

- [ ] **Step 3: Implement**

`citation_notes` is a pure function of the store, so it stays testable without an index or a model. Call it from the `answer` node and append its lines under the existing `Sources:` block.

In `abstain`, for each violation whose rule is `superseded` or `not_governing_on_as_of`: call `store.resolve_authority(v.doc_id, as_of)` and print the governing keys as `doc@rev`; when both the cited and the governing revision have indexed text, append `format_diff(diff_as_of(index, store, v.doc_id, <cited revision's effective_from>, as_of))` limited to the paragraph keys the two revisions disagree on. Guard it so a diff failure degrades to the governing-set line alone — a block must still print its reason even if the diff cannot be computed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kanon/agent.py tests/test_agent.py
git commit -m "feat(agent): citation provenance lines and diff-explained abstentions"
```

---

### Task 6: `diff_revisions` tool with a 10 s budget

**Files:**
- Modify: `kanon/tools.py`
- Modify: `README.md`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: Task 3's `diff_as_of` and `DiffResult`.
- Produces:
  - `DIFF_TIMEOUT_S = 10` module constant in `kanon/tools.py`.
  - `diff_revisions(doc_id: str, as_of_from: date, as_of_to: date) -> DiffResult` as a fifth `@tool`, added to `make_tools`' return list, with a `DiffRevisionsArgs` schema in the same style as the existing arg models (both dates required, no defaults).
  - `run_with_budget(fn: Callable[[], DiffResult], seconds: int = DIFF_TIMEOUT_S) -> DiffResult` — runs `fn` in a worker thread and returns a `DiffResult` carrying a `diff timed out after <n>s` note on expiry.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_with_budget_returns_a_typed_result_on_timeout():
    def slow():
        time.sleep(5)
        raise AssertionError("should not finish")
    r = run_with_budget(slow, seconds=1)          # no raise into the graph
    assert any("timed out" in n for n in r.notes)
    assert (r.gone, r.added, r.changed) == ([], [], [])

def test_run_with_budget_passes_the_result_through():
    r = run_with_budget(lambda: a_result_with_one_gone_paragraph(), seconds=10)
    assert [g.key for g in r.gone] == ["(a)(2)"]

def test_diff_revisions_tool_requires_both_dates():
    tool = next(t for t in make_tools(index, store, "graph") if t.name == "diff_revisions")
    with pytest.raises(ValidationError):
        tool.invoke({"doc_id": "10CFR50.59", "as_of_from": "2000-06-01"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL, `ImportError: cannot import name 'run_with_budget'`

- [ ] **Step 3: Implement**

`run_with_budget` uses `concurrent.futures.ThreadPoolExecutor(max_workers=1)` and `future.result(timeout=seconds)`, catching `TimeoutError`. The worker thread is not killable, so note in a comment that it is abandoned rather than cancelled — acceptable because `diff_as_of` only reads.

```python
# ponytail: the abandoned thread finishes its read in the background; diff_as_of has no writes,
# so there is nothing to roll back. Revisit only if a diff ever mutates state.
```

The tool docstring must state that the result is evidence about paragraph text, not an authority decision — the gate still decides authority. Add the tool to the `README.md` Layer 3 row.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v`
Expected: PASS

- [ ] **Step 5: Full suite and commit**

Run: `uv run pytest -q`
Expected: no new failures versus the pre-change baseline recorded at the start of execution.

```bash
git add kanon/tools.py tests/test_diff.py README.md
git commit -m "feat(tools): diff_revisions tool with a 10s wall-clock budget"
```
