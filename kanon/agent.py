"""Layer 3 — the LangGraph agent, with the Tier B authority gate as a blocking node.

    classify_query ─> select_tools ─> retrieve ─> resolve_authority ─> draft ─> validate
          │ (no date extractable)                                               ╱      ╲
          └──────────────────────────────────────────────────────────────> abstain   answer

Framework: LangGraph StateGraph. The gate has to be a *node with the power to route to
abstention*, and an explicit graph is what makes the trace readable.
  Rejected: AgentExecutor / prebuilt ReAct agents — no place to insert a hard gate in the loop.
  Rejected: a plain tool-calling loop on the raw API — workable, fewer deps, but no callback /
  trace infrastructure; we'd rebuild it by hand.

`draft` sits between resolve_authority and validate (the spec's diagram skips it): the gate
checks *citations*, and citations only exist once an answer has been drafted.

Division of labour — the model never judges authority:
  - LLM: classify the question, pick tools, write the answer, list what it cited.
  - Deterministic code: which documents govern on which date, whether each citation passes.

Single pass, not a loop: select_tools emits all its tool calls in one turn. A multi-turn ReAct
loop would retrieve better on hard questions, but every extra turn is another place for an
unscoped call to slip in, and the trace gets harder to read. ponytail: add a bounded loop
(max 3 rounds, same gate) if eval shows single-pass recall is the bottleneck.
"""

import json
import operator
import re
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.callbacks.manager import dispatch_custom_event
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from kanon.diff import Paragraph, designator_from_section, paragraphs
from kanon.ingest import Chunk
from kanon.retrieval import CorpusIndex, Mode, is_record
from kanon.store import AuthorityStore
from kanon.timing import span
from kanon.tools import make_tools


# ---- structured outputs (with_structured_output; never parse free text) -------------------

class QueryClass(BaseModel):
    kind: Literal["current", "time_anchored"] = Field(
        description="'current' = what governs now. 'time_anchored' = what governed at a past date or event.")
    as_of: date | None = Field(
        default=None,
        description="For time_anchored only: the date the question is about. If only a month or "
                    "year is given, use the first day of that period. Null for current.")
    needs_requirements: bool = Field(
        default=True,
        description="True when the question asks what governs, permits, requires, or approves an action. "
                    "False when it only asks whether an event, violation or citation occurred.")
    plant: str | None = Field(default=None, description="Plant/facility named by the user, if any.")
    unit: str | None = Field(default=None, description="Unit number named by the user, if any.")
    docket: str | None = Field(default=None, description="NRC docket identifier named by the user, if any.")


class Citation(BaseModel):
    doc_id: str
    revision: str
    section: str | None = None
    page: int


class Draft(BaseModel):
    answer: str
    citations: list[Citation] = Field(description="Every source cited in the answer, exactly as labelled.")


class Violation(BaseModel):
    rule: str
    doc_id: str = "—"
    revision: str = "—"
    detail: str


# Authority rules block only in mode 'graph'; in 'none'/'flag' they are *recorded* — that's how
# the superseded-citation rate is measured for the baselines. Integrity rules (nothing cited,
# citing text that was never retrieved) block in every mode: there is no answer to measure.
AUTHORITY_RULES = {"superseded", "not_governing_on_as_of", "unresolved_supersession",
                   "as_of_mismatch", "no_governing_requirement", "no_requirement_citation"}


class AgentState(TypedDict, total=False):
    question: str
    mode: Mode
    today: date
    query_kind: str
    as_of: date | None
    needs_requirements: bool
    plant: str | None
    unit: str | None
    docket: str | None
    tool_calls: list[dict]
    chunks: list[Chunk]
    records: list[str]                 # revision histories / resolutions: context, not citable
    authority: dict[str, bool]         # doc_key -> governs on as_of
    draft: str
    citations: list[Citation]
    violations: Annotated[list[Violation], operator.add]
    decision: Literal["answer", "abstain"]
    final: str


CLASSIFY_PROMPT = """You classify questions from nuclear engineers about NRC requirements.
Today's date is {today}. Decide whether the question asks what governs now ('current') or what
governed at a specific past date or event ('time_anchored'), and extract that date. Also extract
any plant/facility, unit, or docket stated by the user, and whether governing requirements are
needed in addition to historical-event records."""

SELECT_PROMPT = """You gather evidence for a nuclear licensing question.
Authority date: {as_of} ({kind} question).
- For governing requirements call search_current_requirements with as_of={as_of}.
- Pass any plant, unit, and docket identifiers supplied in the question to the search tools.
- If the question asks whether something went wrong before, or about prior events, also call
  find_precedent_events.
- If the question names a specific document, call get_revision_history and/or resolve_supersession.
Make every call you need now, in this single turn."""

DRAFT_PROMPT = """Answer the engineer's question using ONLY the numbered sources below.
- Cite every factual claim with the source's doc_id, revision and page exactly as labelled.
- Sources of kind operating_experience, rulemaking or notice are records of past events or
  changes, not current requirements; say so when used, and never present one as what governs.
- If the sources don't answer part of the question, say what is missing. Do not fill gaps.
- Do not judge which documents are current or authoritative; that is checked separately."""


_UNIT_IN_QUESTION = re.compile(r"(?i)\bunit(?:\s+(?:number|no\.?))?\s*[:#-]?\s*(\d+[A-Za-z]?)\b")
_DOCKET_IN_QUESTION = re.compile(
    r"(?i)\bdocket(?:\s+(?:number|no\.?|id))?\s*[:#]?\s*([A-Z0-9][A-Z0-9.-]{2,30})")
_REQUIREMENT_INTENT = re.compile(
    r"(?i)\b(govern(?:s|ing|ed)?|requirements?|required|approval|approve|permit(?:ted)?|"
    r"10\s*CFR|regulatory\s+guide|\bRG\s*\d|procedure|technical\s+specification)\b")


# A question that only asks whether something happened ("has a licensee been cited ...") is
# answered from records, so requiring a governing-requirement citation would abstain on a correct
# answer (eval q23). It applies only when the question has no requirement wording of its own.
# Rejected: dropping the `10 CFR` trigger above: it flips 13 of 19 eval questions, including
# time-anchored ones where the requirement check is exactly what we want.
_PRECEDENT_ONLY = re.compile(r"(?i)\b(been\s+cited|cited\s+for|violations?|incidents?|has\s+(?:a|any)\s+licensee)\b")
_STRONG_REQUIREMENT = re.compile(r"(?i)\b(govern(?:s|ing|ed)?|requirements?|required|must|permit(?:ted)?|approv\w*)\b")


def _needs_requirements(question: str, model_says: bool) -> bool:
    if _PRECEDENT_ONLY.search(question) and not _STRONG_REQUIREMENT.search(question):
        return False
    return model_says or bool(_REQUIREMENT_INTENT.search(question))


def _question_controls(question: str, qc: QueryClass) -> dict:
    """Controls derived before tool selection; explicit identifiers cannot be dropped by it."""
    unit = (m.group(1) if (m := _UNIT_IN_QUESTION.search(question)) else None) or qc.unit
    docket = (m.group(1) if (m := _DOCKET_IN_QUESTION.search(question)) else None) or qc.docket
    return {"needs_requirements": _needs_requirements(question, qc.needs_requirements),
            "plant": qc.plant, "unit": unit, "docket": docket}


def check_citations(store: AuthorityStore, citations: list[Citation], retrieved: list[Chunk],
                    as_of: date, query_kind: str, *,
                    paragraphs: Callable[[str, str], dict[str, Paragraph]] | None = None) -> list[Violation]:
    """Tier B gate. Pure function, no model: every check is a lookup against recorded metadata."""
    if not citations:
        return [Violation(rule="uncited", detail="answer cites nothing; it cannot be traced")]
    seen = {(c.doc_id, c.revision, c.page) for c in retrieved}
    out: list[Violation] = []
    for cit in citations:
        v = dict(doc_id=cit.doc_id, revision=cit.revision)
        doc = store.get(cit.doc_id, cit.revision)
        if doc is None:
            out.append(Violation(rule="unknown_document", detail="not in the authority store", **v))
            continue
        if (cit.doc_id, cit.revision, cit.page) not in seen:
            out.append(Violation(rule="not_retrieved", detail=f"p.{cit.page} was never retrieved "
                                 "for this query; citation is not grounded in the trace", **v))
        if is_record(doc.kind):
            continue   # records are exempt from the governing check (see retrieval.RECORD_KINDS)
        # Integrity rule (NOT in AUTHORITY_RULES): a paragraph absent from the cited revision's own
        # text is ungrounded in every mode. Unparseable sections yield None and never fire.
        if paragraphs is not None and (key := designator_from_section(cit.section)):
            have = paragraphs(cit.doc_id, cit.revision)
            if key not in have:
                out.append(Violation(rule="paragraph_not_in_revision", detail=(
                    f"{key} does not exist in {cit.doc_id} revision {cit.revision}; that revision "
                    f"has: {', '.join(list(have)[:8]) or 'no extracted paragraphs'}"), **v))
        if not store.governs(cit.doc_id, cit.revision, as_of):
            ending = [f"{e.describe()} — “{e.evidence[:160]}”"
                      for e in store.edges_into(cit.doc_id, cit.revision)
                      if e.valid_from and e.valid_from <= as_of and e.edge_type != "partial"]
            rule = "superseded" if query_kind == "current" else "not_governing_on_as_of"
            out.append(Violation(rule=rule, detail=(
                f"did not govern on {as_of} (recorded status '{doc.status}', effective "
                f"{doc.effective_from} to {doc.effective_to or 'open'})"
                + (f"; ended by {'; '.join(ending)}" if ending else "")), **v))
        for e in store.unresolved_into(cit.doc_id, cit.revision):
            out.append(Violation(rule="unresolved_supersession", detail=(
                f"cannot confirm whether {e.to_doc} Rev {e.to_rev} was superseded by "
                f"{e.from_doc} Rev {e.from_rev} — the supersession record is ambiguous "
                f"(evidence: “{e.evidence[:200]}”)"), **v))
    return out


def build_agent(llm: BaseChatModel, index: CorpusIndex, store: AuthorityStore, mode: Mode):
    tools = make_tools(index, store, mode)
    by_name = {t.name: t for t in tools}

    def classify_query(state: AgentState) -> dict:
        with span("llm.classify"):
            qc = llm.with_structured_output(QueryClass).invoke([
                SystemMessage(CLASSIFY_PROMPT.format(today=state["today"])),
                HumanMessage(state["question"])])
        controls = _question_controls(state["question"], qc)
        if qc.kind == "current":
            return {"query_kind": "current", "as_of": state["today"], **controls}
        if qc.as_of is None:
            return {"query_kind": "time_anchored", "as_of": None, **controls,
                    "violations": [Violation(
                rule="no_as_of", detail="question is time-anchored but no date could be extracted; "
                                        "authority cannot be resolved without one")]}
        return {"query_kind": "time_anchored", "as_of": qc.as_of, **controls}

    def select_tools(state: AgentState) -> dict:
        with span("llm.select_tools"):
            msg = llm.bind_tools(tools).invoke([
                SystemMessage(SELECT_PROMPT.format(as_of=state["as_of"], kind=state["query_kind"])),
                HumanMessage(state["question"])])
        calls = [dict(call, args=dict(call["args"])) for call in msg.tool_calls]
        if state["needs_requirements"] and not any(
                call["name"] == "search_current_requirements" for call in calls):
            calls.append({"name": "search_current_requirements", "id": "required-by-gate",
                          "args": {"query": state["question"],
                                   "as_of": state["as_of"].isoformat()}})
        for call in calls:
            if call["name"] in ("search_current_requirements", "find_precedent_events"):
                for field in ("plant", "unit", "docket"):
                    if state.get(field):
                        call["args"][field] = state[field]
        return {"tool_calls": calls}

    def retrieve(state: AgentState, config: RunnableConfig) -> dict:
        chunks, records, violations = [], [], []
        requirement_hits: list[int] = []   # result size of each search_current_requirements call
        for call in state["tool_calls"]:
            tool = by_name.get(call["name"])
            if tool is None:   # the model named a tool that doesn't exist; record and skip
                records.append(f"ignored unknown tool call {call['name']}")
                continue
            if "as_of" in call["args"] and date.fromisoformat(str(call["args"]["as_of"])) != state["as_of"]:
                violations.append(Violation(rule="as_of_mismatch", detail=(
                    f"{call['name']} called with as_of={call['args']['as_of']} but the question "
                    f"resolves to {state['as_of']}")))
            with span(f"tool.{call['name']}"):
                result = tool.invoke(call["args"], config=config)   # config carries the trace callback
            if call["name"] in ("search_current_requirements", "find_precedent_events"):
                chunks.extend(result)
                if call["name"] == "search_current_requirements":
                    requirement_hits.append(len(result))
            else:
                records.append(f"{call['name']}({call['args']}): {json.dumps(_dump(result), default=str)}")
        if state["needs_requirements"] and (not requirement_hits or not any(requirement_hits)):
            # The agent asked what governed and nothing did. Precedent records may still have been
            # found, but an answer built only from records would answer a requirements question
            # with no requirement — and records are exempt from the governing check, so the gate
            # couldn't catch it later. Block here and name the gap. (Only reachable in 'graph':
            # the baselines always return something, which is exactly their failure mode.)
            violations.append(Violation(rule="no_governing_requirement", detail=(
                f"no requirement document in the store governed on {state['as_of']} for this "
                f"question; the record does not reach back that far")))
        unique = list({c.id: c for c in chunks}.values())
        return {"chunks": unique, "records": records,
                "violations": violations}

    def resolve_authority(state: AgentState, config: RunnableConfig) -> dict:
        """Deterministic graph walk for every retrieved chunk's parent document."""
        authority = {}
        with span("authority.resolve", chunks=len(state["chunks"])):
            for c in state["chunks"]:
                if c.doc_key in authority or is_record(c.kind):
                    continue
                governs = store.governs(c.doc_id, c.revision, state["as_of"])
                res = store.resolve_authority(c.doc_id, state["as_of"])
                authority[c.doc_key] = governs
                dispatch_custom_event("authority_resolution", {
                    "doc_id": c.doc_id, "revision": c.revision, "status": c.status,
                    "as_of": state["as_of"], "governs": governs,
                    "authoritative": res.authoritative, "chain": res.chain,
                    "unresolved": res.unresolved}, config=config)
        return {"authority": authority}

    def draft(state: AgentState) -> dict:
        context = [c for c in state["chunks"]
                   if mode != "graph" or is_record(c.kind) or state["authority"].get(c.doc_key)]
        if not context:
            return {"draft": "", "citations": [], "violations": [Violation(
                rule="no_context", detail=f"no governing document retrieved for as_of={state['as_of']}")]}
        sources = "\n\n".join(
            f"[{i}] doc_id={c.doc_id} | revision={c.revision} | page={c.page} | "
            f"section={c.section or '—'} | kind={c.kind}\n{c.text}" for i, c in enumerate(context, 1))
        records = "\n".join(state.get("records", [])) or "none"
        with span("llm.draft", context_chunks=len(context), prompt_chars=len(sources)):
            d = llm.with_structured_output(Draft).invoke([
                SystemMessage(DRAFT_PROMPT),
                HumanMessage(f"Question: {state['question']}\n\nAuthority records (context only, not "
                             f"citable):\n{records}\n\nSources:\n{sources}")])
        return {"draft": d.answer, "citations": d.citations}

    def validate(state: AgentState, config: RunnableConfig) -> dict:
        cache: dict[tuple[str, str], dict[str, Paragraph]] = {}   # per run: one read per revision

        def revision_paragraphs(doc_id: str, revision: str) -> dict[str, Paragraph]:
            if (doc_id, revision) not in cache:
                cache[doc_id, revision] = paragraphs(index.chunks_for(f"{doc_id}@{revision}"))
            return cache[doc_id, revision]

        new = ([] if not state.get("draft") else
               check_citations(store, state["citations"], state["chunks"], state["as_of"],
                               state["query_kind"], paragraphs=revision_paragraphs))
        if state.get("draft") and state["needs_requirements"]:
            cited_docs = [store.get(c.doc_id, c.revision) for c in state["citations"]]
            if cited_docs and all(doc is None or is_record(doc.kind) for doc in cited_docs):
                new.append(Violation(rule="no_requirement_citation", detail=(
                    "the question asks for governing requirements but the answer cites only records")))
        everything = state.get("violations", []) + new
        enforced = [v for v in everything if mode == "graph" or v.rule not in AUTHORITY_RULES]
        decision = "abstain" if enforced else "answer"
        dispatch_custom_event("gate_decision", {"decision": decision, "enforcing": mode == "graph",
                                                "violations": everything}, config=config)
        return {"violations": new, "decision": decision}

    def answer(state: AgentState) -> dict:
        sources = "\n".join(f"- {c.doc_id} Rev {c.revision}, p.{c.page}"
                            + (f", §{c.section}" if c.section else "") for c in state["citations"])
        return {"final": f"{state['draft']}\n\nSources:\n{sources}"}

    def abstain(state: AgentState) -> dict:
        lines = [f"- {v.doc_id} Rev {v.revision}: [{v.rule}] {v.detail}" if v.doc_id != "—"
                 else f"- [{v.rule}] {v.detail}" for v in state["violations"]]
        return {"decision": "abstain",
                "final": "ABSTAINED — authority could not be verified:\n" + "\n".join(lines)}

    g = StateGraph(AgentState)
    for fn in (classify_query, select_tools, retrieve, resolve_authority, draft, validate, answer, abstain):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "classify_query")
    g.add_conditional_edges("classify_query", lambda s: "abstain" if s["as_of"] is None else "select_tools")
    g.add_edge("select_tools", "retrieve")
    g.add_edge("retrieve", "resolve_authority")
    g.add_edge("resolve_authority", "draft")
    g.add_edge("draft", "validate")
    g.add_conditional_edges("validate", lambda s: s["decision"])
    g.add_edge("answer", END)
    g.add_edge("abstain", END)
    return g.compile()


def _dump(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    return obj
