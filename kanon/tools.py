"""Layer 3 — authority-typed tools.

Why typed tools instead of a prompt rule: "prefer current revisions" is a soft instruction a
model can ignore. A tool whose signature *requires* `as_of` makes the unsafe query — "search
everything, whatever date" — inexpressible.

  - `as_of` has NO default. The agent must commit to a date; that is the enforcement.
  - There is deliberately NO unscoped `search_all_documents` tool. Removing it is the point.
  - `find_precedent_events` is the one deliberate exception to the authority rule (see its
    docstring) — an interviewer will ask why this tool breaks the rule.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from kanon.diff import DiffResult, diff_as_of
from kanon.ingest import Chunk
from kanon.retrieval import CorpusIndex, Mode
from kanon.store import AuthorityResult, AuthorityStore, DocumentVersion, Edge

DIFF_TIMEOUT_S = 10   # wall-clock budget for one diff; here, not config.py, so it sits beside its only user


def run_with_budget(fn: Callable[[], DiffResult], seconds: int = DIFF_TIMEOUT_S, *,
                    doc_id: str = "", from_as_of: date = date.min,
                    to_as_of: date = date.min) -> DiffResult:
    """Run `fn` in a worker thread; on expiry return a typed result instead of raising, so a
    hanging diff degrades to "could not compute" rather than killing the graph run.

    The request context is passed in rather than patched onto the result afterwards, so a timed-out
    diff still says which document and dates it gave up on.
    """
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=seconds)
    except FutureTimeout:
        # ponytail: the abandoned thread finishes its read in the background; diff_as_of has no
        # writes, so there is nothing to roll back. Revisit only if a diff ever mutates state.
        return DiffResult(doc_id=doc_id, from_as_of=from_as_of, to_as_of=to_as_of,
                          from_revisions=[], to_revisions=[],
                          notes=[f"diff timed out after {seconds}s"])
    finally:
        ex.shutdown(wait=False)


def _thread_store(store: AuthorityStore) -> AuthorityStore:
    """A store the worker thread may use. sqlite3 connections belong to the thread that opened
    them, so the diff worker opens its own against the same file; reads need no coordination.
    An in-memory store has no file to reopen and is a test-only path, so it is passed through.
    """
    return store if store.path == ":memory:" else AuthorityStore(store.path)


class RevisionAuthority(BaseModel):
    doc_id: str
    revision: str
    governs_on_as_of: bool          # did THIS revision govern on as_of
    ended_by: list[Edge]            # dated, non-partial edges into this revision
    authority: AuthorityResult      # what governs the subject matter on as_of


class CurrentRequirementsArgs(BaseModel):
    query: str = Field(description="What requirement to look for, in regulatory language.")
    as_of: date = Field(description="The date the requirements must have governed on. Required. "
                                    "Use today's date for questions about current requirements.")
    plant: str | None = None
    unit: str | None = None
    docket: str | None = None


class RevisionHistoryArgs(BaseModel):
    doc_id: str = Field(description="Document id, e.g. 'RG-1.187', '10CFR50.59'.")


class ResolveSupersessionArgs(BaseModel):
    doc_id: str
    revision: str
    as_of: date = Field(description="Date to resolve authority on. Required.")


class PrecedentArgs(BaseModel):
    system: str = Field(description="Plant system or component, e.g. 'primary coolant pump'.")
    symptom: str = Field(description="What went wrong or what change was made, e.g. 'seal leak', "
                                     "'inadequate 50.59 evaluation'.")
    since: date | None = Field(default=None, description="Only events on/after this date.")
    plant: str | None = None
    unit: str | None = None
    docket: str | None = None


class DiffRevisionsArgs(BaseModel):
    doc_id: str = Field(description="Document id, e.g. '10CFR50.59'.")
    as_of_from: date = Field(description="Earlier date to compare. Required.")
    as_of_to: date = Field(description="Later date to compare. Required.")


def make_tools(index: CorpusIndex, store: AuthorityStore, mode: Mode, k: int = 8) -> list[BaseTool]:
    """Tools are closures over one index/store/mode so the eval harness can run the same agent
    in all three modes without the model ever seeing (or choosing) the mode."""

    @tool(args_schema=CurrentRequirementsArgs)
    def search_current_requirements(query: str, as_of: date, plant: str | None = None,
                                    unit: str | None = None, docket: str | None = None) -> list[Chunk]:
        """Search only documents governing on `as_of` (regulations, guides, tech specs,
        amendments). `as_of` is REQUIRED."""
        return index.search(query, k, mode, as_of, plant=plant, unit=unit, docket=docket)

    @tool(args_schema=RevisionHistoryArgs)
    def get_revision_history(doc_id: str) -> list[DocumentVersion]:
        """Full revision chain of one document, newest first, with status per revision."""
        return store.revision_history(doc_id)

    @tool(args_schema=ResolveSupersessionArgs)
    def resolve_supersession(doc_id: str, revision: str, as_of: date) -> RevisionAuthority:
        """Report store lookups for one document revision: whether that revision governed on
        `as_of`, the dated non-partial supersession/withdrawal edges that point into it, and the
        documents the store lists as governing the same subject matter on `as_of`. These are
        records, not a ruling; the authority gate decides what may be cited."""
        # Spec draft had no as_of here ("what governs now"). Added it: "now" is just one value of
        # as_of, and an implicit today() would be the only date in the system nobody chose.
        return RevisionAuthority(
            doc_id=doc_id, revision=revision,
            governs_on_as_of=store.governs(doc_id, revision, as_of),
            ended_by=[e for e in store.edges_into(doc_id, revision)
                      if e.valid_from is not None and e.edge_type != "partial"],
            authority=store.resolve_authority(doc_id, as_of))

    @tool(args_schema=PrecedentArgs)
    def find_precedent_events(system: str, symptom: str, since: date | None = None,
                              plant: str | None = None, unit: str | None = None,
                              docket: str | None = None) -> list[Chunk]:
        """Search the historical record for prior occurrences: operating experience (inspection
        reports, enforcement actions, event reports) and rulemaking history (Federal Register
        rules and notices).

        Deliberate exception to the authority rule: searches hot AND cold. Superseded status does
        NOT disqualify a record, because it states what happened, not what governs. A 1998
        enforcement action is still true about 1996; it would only be wrong to cite it as a
        current requirement — and the gate exempts only the explicit RECORD_KINDS from the
        governing check, nothing else."""
        return index.search_precedent(f"{system} {symptom}", k, since, plant=plant, unit=unit, docket=docket)

    @tool(args_schema=DiffRevisionsArgs)
    def diff_revisions(doc_id: str, as_of_from: date, as_of_to: date) -> DiffResult:
        """Compare the paragraph text of the revisions of one document that governed on two dates:
        paragraphs gone, added, or changed between them. Both dates are REQUIRED. The result is
        EVIDENCE about what the text said, not an authority decision; the authority gate still
        decides what may be cited. Gives up after 10 seconds with a note instead of an answer."""
        return run_with_budget(lambda: diff_as_of(index, _thread_store(store), doc_id,
                                                  as_of_from, as_of_to),
                               doc_id=doc_id, from_as_of=as_of_from, to_as_of=as_of_to)

    return [search_current_requirements, get_revision_history, resolve_supersession,
            find_precedent_events, diff_revisions]
