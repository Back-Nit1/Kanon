"""Tier C — answer judge. LLM, ADVISORY ONLY: annotates, never blocks, never edits.

Tier B (agent.check_citations) holds all blocking authority. An LLM must not be able to
override a deterministic check, so the judge is wired so that it *can't*:
  1. `review` works on a deep copy of the agent state. Whatever happens inside, the caller's
     answer, citations and gate decision are untouched objects.
  2. It returns a separate `JudgeReport`; it has no return path into the state.
  3. `append_to_trace` only *adds* a `tier_c` key to the trace JSON and appends a section to the
     trace Markdown. It refuses to overwrite an existing `tier_c`.
  Rejected: a LangGraph node after `validate`. It would put the judge inside the graph that owns
  the decision — one edge change away from routing on the judge's output.

Checks (spec, Layer 4): overclaim relative to evidence, answers a different question, uncited
claims, skipped reasoning step. Output via with_structured_output; never parse free text.

KNOWN LIMITATION — same-provider judge: by default JUDGE_MODEL == RAG_MODEL (same provider,
often the same model). A model grading its own family shares its blind spots and its stylistic
preferences, so agreement here is weak evidence. The fix is one env var: point JUDGE_MODEL at a
different provider (config.Settings). Because the judge is advisory, this limitation can lower
the value of the annotations but cannot cause a wrong citation to pass.
"""

import copy
import json
from pathlib import Path
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field


class Annotation(BaseModel):
    check: Literal["overclaim", "different_question", "uncited_claim", "skipped_step"]
    quote: str = Field(description="The exact span of the answer this annotation is about.")
    detail: str = Field(description="What is wrong, referring to the sources by doc_id/revision/page.")


class JudgeReport(BaseModel):
    annotations: list[Annotation] = Field(description="Empty if the answer has none of the problems.")
    summary: str = Field(description="One or two sentences.")


JUDGE_PROMPT = """You review an answer produced by a retrieval system for nuclear engineers.
You are advisory: you do not decide whether the answer is released and you do not judge whether
cited documents are current (a deterministic check already did that). Report only:
- overclaim: the answer states more, or more confidently, than the sources support
- different_question: the answer addresses a different question from the one asked
- uncited_claim: a factual claim with no citation
- skipped_step: the reasoning jumps over a step needed to reach its conclusion
Quote the exact span you are annotating. If there are no problems, return no annotations."""


def review(llm: BaseChatModel, state: dict) -> JudgeReport:
    """Annotate a completed agent run. Never mutates `state` (works on a deep copy)."""
    view = copy.deepcopy(state)
    if view.get("decision") != "answer":
        # Nothing was released, so there is no answer to judge. The abstention text is generated
        # from Tier B violations, not by a model, and needs no LLM review.
        return JudgeReport(annotations=[], summary="abstained; nothing to judge")
    cited = {(c.doc_id, c.revision, c.page) for c in view.get("citations", [])}
    sources = "\n\n".join(
        f"[{c.doc_id} Rev {c.revision} p.{c.page}{' (cited)' if (c.doc_id, c.revision, c.page) in cited else ''}]"
        f"\n{c.text}" for c in view.get("chunks", []))
    return llm.with_structured_output(JudgeReport).invoke([
        SystemMessage(JUDGE_PROMPT),
        HumanMessage(f"Question: {view['question']}\n\nAnswer:\n{view.get('final', '')}\n\n"
                     f"Retrieved sources:\n{sources or 'none'}")])


def append_to_trace(trace_md: Path, report: JudgeReport, judge_model: str) -> None:
    """Append annotations to an existing trace (.md + sibling .json). Append-only."""
    trace_json = trace_md.with_suffix(".json")
    original = json.loads(trace_json.read_text())
    if "tier_c" in original:
        raise ValueError(f"{trace_json} already has Tier C annotations; refusing to overwrite")
    record = {**original, "tier_c": {"advisory": True, "judge_model": judge_model,
                                     **report.model_dump()}}
    trace_json.write_text(json.dumps(record, indent=2))

    lines = ["", "## 6. Tier C judge (advisory — did not affect the decision above)", "",
             f"- Judge model: `{judge_model}` (see judge.py: same-provider limitation)",
             f"- Summary: {report.summary}"]
    lines += [f"- `{a.check}`: “{a.quote}” — {a.detail}" for a in report.annotations] or ["- no annotations"]
    with trace_md.open("a") as f:   # append mode: the existing trace text cannot be rewritten
        f.write("\n".join(lines) + "\n")
