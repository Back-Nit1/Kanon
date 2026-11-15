"""Layer 3 — authority-typed tools.

Why typed tools instead of a prompt rule: "prefer current revisions" is a soft instruction a
model can ignore. A tool whose signature *requires* `as_of` makes the unsafe query — "search
everything, whatever date" — inexpressible.

  - `as_of` has NO default. The agent must commit to a date; that is the enforcement.
  - There is deliberately NO unscoped `search_all_documents` tool. Removing it is the point.
  - `find_precedent_events` is the one deliberate exception to the authority rule (see its
    docstring) — an interviewer will ask why this tool breaks the rule.
"""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from kanon.ingest import Chunk
from kanon.retrieval import CorpusIndex, Mode
from kanon.store import AuthorityResult, AuthorityStore, DocumentVersion


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
    def resolve_supersession(doc_id: str, revision: str, as_of: date) -> AuthorityResult:
        """What superseded this document revision, how (full/partial/cross-family/withdrawn),
        and what governs its subject matter on `as_of`."""
        # Spec draft had no as_of here ("what governs now"). Added it: "now" is just one value of
        # as_of, and an implicit today() would be the only date in the system nobody chose.
        return store.resolve_authority(doc_id, as_of)

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

    return [search_current_requirements, get_revision_history, resolve_supersession,
            find_precedent_events]
