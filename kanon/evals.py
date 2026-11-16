"""Evaluation harness: the same pinned question set through modes none / flag / graph.

    uv run kanon-eval                    # retrieval metrics + full agent runs, all three modes
    uv run kanon-eval --retrieval-only   # no chat model needed (still needs the embedder)
    uv run kanon-eval --judge            # also append Tier C annotations to every trace
    uv run kanon-eval --modes none --rerank   # the KANON.md A/B: baseline with and without the
                                              # cross-encoder reranker, retrieval and generation

Three levels, kept separate on purpose (never pooled into one number):
  - Retrieval (CorpusIndex.search per mode, no LLM): recall@k, MRR, and superseded-chunk rate —
    the share of retrieved chunks whose document did not govern on the question's as_of.
  - Generation (the full agent): superseded-citation rate, ALWAYS paired with answer rate. A
    system that abstains on everything scores 0% superseded and is useless; the pair is the
    metric. `pair()` is the only function that formats either number, so neither can be printed
    alone.
  - Resolver (store level, deterministic, no LLM): for `type: resolver` questions, is
    resolve_authority(doc_id, as_of) the expected set, and is it unresolved exactly when it should
    be? These exist because reg guides are metadata-only rows (no text to retrieve or cite).
    `flag` has no resolver, so its stand-in is the status column today (revisions marked active).
    That is a faithful model of a status filter, but it understates a flag RAG system, which could
    still read a successor's name from a withdrawal notice's prose. The report says so.

Everything that decides "superseded" is `store.governs` against the question's PINNED as_of,
never the model's own date and never a model's judgment. If the model misreads the date, that
shows up as `as_of_mismatch` in the rows rather than silently moving the goalposts.
  Rejected: scoring with Ragas faithfulness / context precision as the headline. They grade
  grounding and topicality, and a superseded revision is perfectly grounded and topical — they
  read green on exactly the failure this project is about. (Optional add-on; not wired in.)

Reproducibility: `today` is pinned in questions.yaml and passed to the agent, and the report
records the SHA-256 of the question file so a reader can tell which pinned set produced it.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from kanon.ingest import Chunk
from kanon.ratelimit import BudgetExceeded, used
from kanon.retrieval import CorpusIndex, Reranker, is_record
from kanon.store import AuthorityStore
from kanon.timing import QuestionTimeout, deadline, set_context, span

MODES = ("none", "flag", "graph")
# The cases where a status flag is structurally blind and the bi-temporal graph is not. The spec
# names the first three; effective-AFTER-issue is the real variant this corpus has, listed under
# its own name so it is never passed off as the spec's "precedes" case.
THESIS_CATEGORIES = {"partial": "partial supersession",
                     "cross_family": "cross-family supersession",
                     "effective_before_issue": "effective date precedes issue date",
                     "effective_after_issue": "effective date after issue date (not the spec's named case)"}
EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


class Question(BaseModel):
    id: str
    # qa: retrieval + agent in all modes. resolver: store-level check on doc_id, no LLM.
    # Rejected: a separate resolver file. One pinned file, one sha256, one thing to freeze.
    type: Literal["qa", "resolver"] = "qa"
    category: str
    query_kind: Literal["current", "time_anchored"]
    as_of: date | None = None       # required for time_anchored; must be absent for current
    question: str
    # "DOC@REV" keys (same form as Chunk.doc_key) that govern the answer on as_of.
    # Empty = no governing document exists in the corpus: the correct behaviour is to abstain.
    expected: list[str]
    doc_id: str | None = None       # resolver only: the document whose authority is resolved
    expect_resolved: bool = True    # resolver only: False = the correct output is "unresolved"

    def authority_date(self, today: date) -> date:
        return self.as_of or today


def load_questions(path: Path) -> tuple[date, list[Question], str, list[str]]:
    """-> (pinned today, questions, sha256 of the file, coverage notes for the report)"""
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    return (date.fromisoformat(str(data["today"])),
            [Question.model_validate(q) for q in data["questions"]],
            hashlib.sha256(raw).hexdigest(), data.get("coverage_notes", []))


def check_questions(store: AuthorityStore, today: date, questions: list[Question]) -> list[str]:
    """Every expected answer must be derivable from the store: it exists and governs on as_of.
    A question that fails this would be grading the system against our own mistake."""
    problems = []
    if len({q.id for q in questions}) != len(questions):
        problems.append("duplicate question ids")
    for q in questions:
        if (q.query_kind == "time_anchored") != (q.as_of is not None):
            problems.append(f"{q.id}: time_anchored questions need as_of; current ones must not set it")
        if (q.type == "resolver") != (q.doc_id is not None):
            problems.append(f"{q.id}: resolver questions need doc_id; qa questions must not set it")
        elif q.type == "resolver" and not store.revision_history(q.doc_id):
            problems.append(f"{q.id}: doc_id {q.doc_id} is not in the authority store")
        if q.type == "qa" and q.category != "control" and q.expected and all(
                (d := store.get(*k.split("@", 1))) is not None and is_record(d.kind) for k in q.expected):
            # A record (FR rule/notice, enforcement letter) states what happened; it never governs.
            problems.append(f"{q.id}: expected set has only records; the governing answer must be a requirement")
        for key in q.expected:
            doc_id, _, rev = key.partition("@")
            d = store.get(doc_id, rev)
            if d is None:
                problems.append(f"{q.id}: expected {key} is not in the authority store")
            elif not is_record(d.kind) and not store.governs(doc_id, rev, q.authority_date(today)):
                problems.append(f"{q.id}: expected {key} does not govern on {q.authority_date(today)}")
    return problems


def governs(store: AuthorityStore, doc_id: str, revision: str, as_of: date) -> bool:
    """Same rule as the Tier B gate: event records are exempt; a document missing from the store
    did not govern (it can't be shown to)."""
    d = store.get(doc_id, revision)
    return d is not None and (is_record(d.kind) or store.governs(doc_id, revision, as_of))


# ---- retrieval level ----------------------------------------------------------------------

def retrieval_row(q: Question, chunks: list[Chunk], store: AuthorityStore, as_of: date) -> dict:
    keys = [c.doc_key for c in chunks]
    # CorpusIndex.search never returns operating experience (that's search_precedent's job), so
    # an expected event record would cap recall for a reason that isn't a retrieval failure.
    # Event records are still scored at the generation level (cites_expected).
    expected = {k for k in q.expected
                if (d := store.get(*k.split("@", 1))) is None or not is_record(d.kind)}
    first = next((i for i, key in enumerate(keys, 1) if key in expected), None)
    return {"id": q.id, "category": q.category, "retrieved": keys,
            # recall over documents, not chunks: did each expected (doc, rev) show up at all?
            "recall": len(expected & set(keys)) / len(expected) if expected else None,
            "rr": (1 / first if first else 0.0) if expected else None,
            "non_governing": sum(not governs(store, c.doc_id, c.revision, as_of) for c in chunks)}


def summarise_retrieval(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["recall"] is not None]   # expected-abstain questions excluded
    retrieved = sum(len(r["retrieved"]) for r in rows)
    return {"questions": len(rows), "scored": len(scored),
            "recall_at_k": sum(r["recall"] for r in scored) / len(scored) if scored else 0.0,
            "mrr": sum(r["rr"] for r in scored) / len(scored) if scored else 0.0,
            # pooled over every retrieved chunk, so a question with many hits weighs more
            "superseded_chunk_rate": sum(r["non_governing"] for r in rows) / retrieved if retrieved else 0.0,
            "retrieved_chunks": retrieved}


# The reranker A/B (KANON.md, Reranking) runs through `index.reranker`, i.e. the production path,
# not a parallel eval-only code path — see `run(rerank=...)`. `label` only names the row group in
# the report and traces ("none" vs "none+rerank"); `mode` is still the authority mode.
# Never compare raw reranker scores across models (BGE sigmoid vs Cohere vs ColBERT MaxSim live on
# different scales) — compare only the metrics below.


def retrieval_eval(index: CorpusIndex, store: AuthorityStore, today: date,
                   questions: list[Question], mode: str, k: int,
                   label: str | None = None) -> list[dict]:
    rows = []
    for q in questions:
        as_of = q.authority_date(today)
        set_context(phase="retrieval", mode=label or mode, qid=q.id)
        with span("eval.retrieval_question"):
            chunks = index.search(q.question, k, mode, as_of)
        rows.append(retrieval_row(q, chunks, store, as_of))
    return rows


# ---- generation level ---------------------------------------------------------------------

def generation_row(q: Question, state: dict, store: AuthorityStore, as_of: date) -> dict:
    citations = state.get("citations", []) if state.get("decision") == "answer" else []
    answered = state.get("decision") == "answer"
    return {"id": q.id, "category": q.category, "decision": state.get("decision"),
            "expect_abstain": not q.expected,
            "superseded": answered and any(not governs(store, c.doc_id, c.revision, as_of)
                                           for c in citations),
            "cites_expected": answered and any(f"{c.doc_id}@{c.revision}" in q.expected
                                               for c in citations),
            "citations": [f"{c.doc_id}@{c.revision} p.{c.page}" for c in citations],
            "model_as_of": str(state.get("as_of")), "as_of_mismatch": state.get("as_of") != as_of,
            "violations": [v.rule for v in state.get("violations", [])]}


def summarise_generation(rows: list[dict]) -> dict:
    n = len(rows)
    answered = sum(r["decision"] == "answer" for r in rows)
    return {"questions": n, "answered": answered,
            # A timeout is neither an answer nor an abstention: the system never got to decide.
            # It counts as "not answered" in the rate but is reported separately so a slow run is
            # never read as a cautious one.
            "timed_out": sum(r["decision"] == "timeout" for r in rows),
            # Same treatment for a question the provider or the stack failed on.
            "errored": sum(r["decision"] == "error" for r in rows),
            "answer_rate": answered / n if n else 0.0,
            # Over ANSWERED questions. With nothing answered it is 0.0 — meaningless on its own,
            # which is exactly why it is only ever reported next to answer_rate.
            "superseded_citation_rate": sum(r["superseded"] for r in rows) / answered if answered else 0.0,
            "cites_expected_rate": sum(r["cites_expected"] for r in rows) / answered if answered else 0.0}


def pair(s: dict) -> str:
    """The one place either half of the primary metric is formatted. Never split it."""
    late = f", {s['timed_out']} TIMED OUT" if s.get("timed_out") else ""
    late += f", {s['errored']} FAILED" if s.get("errored") else ""
    return (f"superseded-citation rate {s['superseded_citation_rate']:.0%} "
            f"@ answer rate {s['answer_rate']:.0%} ({s['answered']}/{s['questions']} answered{late})")


def generation_eval(llm, index: CorpusIndex, store: AuthorityStore, today: date,
                    questions: list[Question], mode: str, trace_dir: Path,
                    judge_llm=None, judge_model: str = "",
                    question_timeout: float | None = None,
                    label: str | None = None) -> list[dict]:
    from kanon.agent import build_agent
    from kanon.trace import TraceRecorder
    agent = build_agent(llm, index, store, mode)
    rows = []
    for q in questions:
        recorder = TraceRecorder()
        set_context(phase="generation", mode=label or mode, qid=q.id)
        try:
            with span("eval.generation_question"), deadline(question_timeout):
                state = agent.invoke({"question": q.question, "mode": mode, "today": today},
                                     config={"callbacks": [recorder]})
        # QuestionTimeout is a BaseException, so both arms are needed. One transient provider
        # error (a dropped connection mid-run) must not destroy the other 50 rows: record the
        # question as 'error', keep the partial trace, and carry on. An error is NOT an
        # abstention — the system never reached a gate decision — so it is reported separately
        # and excluded from the named failure cases, exactly like a timeout.
        except BudgetExceeded:
            # Not a failed question: the run is out of its request budget and nothing was sent.
            # Recording it per question would mark every remaining row 'error' and hide why.
            raise
        except (QuestionTimeout, Exception) as exc:
            timed_out = isinstance(exc, QuestionTimeout)
            decision = "timeout" if timed_out else "error"
            with span(f"eval.{decision}"):
                pass
            state = {"question": q.question, "mode": mode, "decision": decision,
                     "final": (f"TIMED OUT after {question_timeout:g}s" if timed_out
                               else f"FAILED: {type(exc).__name__}: {exc}")
                              + "; no answer or gate decision."}
            print(f"  {q.id} [{label or mode}] {state['final']}", file=sys.stderr)
            path = recorder.write(trace_dir / mode / q.id, state)
            rows.append({"id": q.id, "category": q.category, "decision": decision,
                         "expect_abstain": not q.expected, "superseded": False,
                         "cites_expected": False, "citations": [], "model_as_of": "—",
                         "as_of_mismatch": False, "violations": [], "trace": str(path)})
            continue
        # One directory per question: TraceRecorder names files by the second, so two fast
        # questions in one directory would overwrite each other.
        path = recorder.write(trace_dir / mode / q.id, state)
        if judge_llm is not None:
            from kanon.judge import append_to_trace, review
            with span("llm.judge"):
                append_to_trace(path, review(judge_llm, state), judge_model)
        rows.append({**generation_row(q, state, store, q.authority_date(today)), "trace": str(path)})
    return rows


# ---- resolver level -----------------------------------------------------------------------

def resolver_row(q: Question, store: AuthorityStore, today: date) -> dict:
    res = store.resolve_authority(q.doc_id, q.authority_date(today))
    graph = [f"{d}@{r}" for d, r in res.authoritative]
    # flag's only knowledge: which revisions carry status 'active' right now. No dates, no edges.
    flag = [f"{v.doc_id}@{v.revision}" for v in store.revision_history(q.doc_id) if v.status == "active"]
    expected = set(q.expected)
    return {"id": q.id, "category": q.category, "doc_id": q.doc_id,
            "as_of": str(q.authority_date(today)), "expected": q.expected,
            "expect_resolved": q.expect_resolved,
            "graph": graph, "graph_resolved": res.resolved,
            # unresolved expected -> correct iff the resolver refuses; otherwise exact set match
            "graph_correct": (not res.resolved) if not q.expect_resolved
                             else (res.resolved and set(graph) == expected),
            "unresolved": [e.describe() for e in res.unresolved],
            "flag": flag,
            # a status column can't say "ambiguous", so it is wrong whenever abstention is correct
            "flag_correct": q.expect_resolved and set(flag) == expected}


def summarise_resolver(rows: list[dict]) -> dict:
    n = len(rows)
    return {"questions": n,
            "graph_correct": sum(r["graph_correct"] for r in rows),
            "flag_correct": sum(r["flag_correct"] for r in rows)}


# ---- named failure cases ------------------------------------------------------------------

def failure_cases(retrieval: dict, generation: dict | None, resolver: list[dict] | None = None) -> list[dict]:
    """Questions where `flag` fails and `graph` succeeds. Computed, never asserted: if this list
    is empty the report says so. Each case carries its level (retrieval/generation/resolver)."""
    cases = [{"id": r["id"], "category": r["category"], "level": "resolver",
              "flag": f"status column says {r['flag'] or 'nothing active'}",
              "graph": "unresolved (abstains)" if not r["graph_resolved"] else f"{r['graph']}"}
             for r in resolver or [] if r["graph_correct"] and not r["flag_correct"]]
    r_flag = {r["id"]: r for r in retrieval.get("flag", {}).get("rows", [])}
    for g in retrieval.get("graph", {}).get("rows", []):
        f = r_flag.get(g["id"])
        if f is None or g["recall"] is None:
            continue
        graph_ok = g["recall"] > 0 and g["non_governing"] == 0
        flag_bad = f["recall"] < g["recall"] or f["non_governing"] > 0
        if graph_ok and flag_bad:
            cases.append({"id": g["id"], "category": g["category"], "level": "retrieval",
                          "flag": f"recall {f['recall']:.2f}, {f['non_governing']} non-governing chunks",
                          "graph": f"recall {g['recall']:.2f}, 0 non-governing chunks"})
    if generation:
        g_flag = {r["id"]: r for r in generation.get("flag", {}).get("rows", [])}
        for g in generation.get("graph", {}).get("rows", []):
            f = g_flag.get(g["id"])
            if f is None or {f["decision"], g["decision"]} & {"timeout", "error"}:
                continue   # neither says anything about authority; never score it as a failure
            if g["expect_abstain"]:
                graph_ok, flag_bad = g["decision"] == "abstain", f["decision"] == "answer"
            else:
                graph_ok = g["decision"] == "answer" and g["cites_expected"] and not g["superseded"]
                flag_bad = f["superseded"] or not f["cites_expected"]
            if graph_ok and flag_bad:
                cases.append({"id": g["id"], "category": g["category"], "level": "generation",
                              "flag": f"{f['decision']}, cited {f['citations'] or 'nothing'}",
                              "graph": f"{g['decision']}, cited {g['citations'] or 'nothing'}"})
    return cases


# ---- report -------------------------------------------------------------------------------

def render_report(res: dict) -> str:
    L = ["# Kanon evaluation report", "",
         f"- Question set: `{res['questions_file']}` (sha256 `{res['questions_sha256'][:12]}`), "
         f"{res['n_questions']} questions ({res.get('n_qa', res['n_questions'])} qa, "
         f"{res.get('n_resolver', 0)} resolver), pinned today = {res['today']}, k = {res['k']}",
         f"- Categories: {', '.join(f'{c} ({n})' for c, n in sorted(res['categories'].items()))}", ""]

    L += ["## Primary metric (generation): superseded-citation rate, paired with answer rate", ""]
    if res["generation"] is None:
        L += ["Not run (`--retrieval-only`, or no chat model configured). No superseded-citation "
              "rate is reported because it cannot be reported without its answer rate.", ""]
    else:
        L += ["| mode | superseded-citation rate | answer rate | answered | cites an expected doc (of answered) |",
              "|---|---|---|---|---|"]
        for m, g in res["generation"].items():
            s = g["summary"]
            L.append(f"| {m} | {s['superseded_citation_rate']:.0%} | {s['answer_rate']:.0%} | "
                     f"{s['answered']}/{s['questions']} | {s['cites_expected_rate']:.0%} |")
        L += ["", *[f"- `{m}`: {pair(g['summary'])}" for m, g in res["generation"].items()], ""]
        if any(g["summary"].get("timed_out") or g["summary"].get("errored")
               for g in res["generation"].values()):
            L += ["**Some questions timed out or failed.** Neither is an abstention; those rows have "
                  "no answer and no gate decision, and are excluded from named failure cases. A "
                  "failed row means the provider or the stack errored, not that the system refused.", ""]

    L += ["## Retrieval (no LLM)", "",
          "| mode | recall@k | MRR | superseded-chunk rate | retrieved chunks |", "|---|---|---|---|---|"]
    for m, r in res["retrieval"].items():
        s = r["summary"]
        L.append(f"| {m} | {s['recall_at_k']:.2f} | {s['mrr']:.2f} | {s['superseded_chunk_rate']:.0%} "
                 f"| {s['retrieved_chunks']} |")
    L += ["", "Recall@k and MRR are over the questions with a non-empty expected set; "
          "superseded-chunk rate is pooled over every retrieved chunk.", ""]

    rows = res.get("resolver", {}).get("rows", [])
    L += ["## Resolver (store level, deterministic, no LLM) — separate from the two blocks above", ""]
    if not rows:
        L += ["No resolver questions in the pinned set.", ""]
    else:
        s = res["resolver"]["summary"]
        L += [f"- graph (bi-temporal resolver): {s['graph_correct']}/{s['questions']} correct",
              f"- flag (status column today): {s['flag_correct']}/{s['questions']} correct",
              "- `none` has no authority information at all, so it has no resolver row.",
              "- Caveat: these are reg guides with metadata but no text. The flag stand-in is the "
              "status column, which a flag RAG system could beat by reading a withdrawal notice's "
              "prose. Read this block as 'what each authority model knows', not as RAG accuracy.",
              "- Not independent: expected sets were written from the evidence strings and then "
              "checked against this same store, so graph agreement confirms the parser and resolver "
              "encode the evidence. The informative column is flag's.", "",
              "| id | category | doc @ as_of | expected | graph | flag (today's status) |",
              "|---|---|---|---|---|---|"]
        for r in rows:
            exp = r["expected"] if r["expect_resolved"] else "unresolved -> abstain"
            g = r["graph"] if r["graph_resolved"] else "unresolved"
            L.append(f"| {r['id']} | {r['category']} | {r['doc_id']} @ {r['as_of']} | {exp} | "
                     f"{g} {'✓' if r['graph_correct'] else '✗'} | {r['flag'] or '—'} "
                     f"{'✓' if r['flag_correct'] else '✗'} |")
        L.append("")

    L += ["## Named cases: flag fails, graph succeeds", ""]
    for cat, label in THESIS_CATEGORIES.items():
        found = [c for c in res["failure_cases"] if c["category"] == cat]
        if not res["categories"].get(cat):
            L.append(f"- {label}: **not tested** — no question in this category (see coverage notes).")
        elif not found:
            L.append(f"- {label}: **no case found** where `flag` fails and `graph` succeeds.")
        L += [f"- {label}: **{c['id']}** ({c['level']}) — flag: {c['flag']}; graph: {c['graph']}"
              for c in found]
    other = [c for c in res["failure_cases"] if c["category"] not in THESIS_CATEGORIES]
    if other:
        L += ["", "Other categories (`flag` only knows what is active today, so any past as_of "
              "can trip it; these are real but are not the three thesis cases):"]
        L += [f"- {c['id']} ({c['category']}, {c['level']}) — flag: {c['flag']}; graph: {c['graph']}"
              for c in other]
    if res.get("coverage_notes"):
        L += ["", "## Coverage notes (from the pinned question file)", ""]
        L += [f"- {n}" for n in res["coverage_notes"]]
    L += ["", "## Notes", "",
          "- 'Superseded' is decided by `AuthorityStore.governs` against each question's pinned "
          "as_of — never by a model. Records (operating experience, FR rules and notices) are "
          "exempt: they record what happened, not what governs.",
          ("- Reranker: every mode above appears twice, `<mode>` and `<mode>+rerank` "
           "(cross-encoder over the top ~100 pre-filtered candidates). Compare the pair, not the "
           "raw reranker scores." if res.get("reranked") else
           "- Reranker run (`--rerank`, cross-encoder over the top ~100 pre-filtered candidates): "
           "not run in this pass; off unless KANON_RERANK_MODEL is set."),
          "- Tier C judge annotations, when enabled, are advisory and live in the traces; they "
          "do not feed any metric here. Judge shares a provider with the RAG model by default.", ""]
    return "\n".join(L)


def run(index: CorpusIndex, store: AuthorityStore, questions_file: Path, k: int,
        modes: tuple[str, ...] = MODES, llm=None, trace_dir: Path | None = None,
        judge_llm=None, judge_model: str = "", question_timeout: float | None = None,
        rerank: Reranker | None = None) -> dict:
    """With `rerank`, every requested mode is measured twice — `<mode>` and `<mode>+rerank` —
    because a reranker is only trustworthy here if the paired numbers say so (KANON.md:
    better topical ranking may *raise* the superseded-citation rate, since a superseded revision
    is topically near-identical to the current one). Pass `--modes none` to A/B only the baseline.
    """
    today, questions, sha, notes = load_questions(questions_file)
    problems = check_questions(store, today, questions)
    if problems:   # halt: grading against an underivable expected answer is worse than no grade
        raise ValueError("question set does not match the authority store:\n  " + "\n  ".join(problems))
    qa = [q for q in questions if q.type == "qa"]
    resolver = [resolver_row(q, store, today) for q in questions if q.type == "resolver"]
    res = {"questions_file": str(questions_file), "questions_sha256": sha, "today": str(today),
           "k": k, "n_questions": len(questions), "n_qa": len(qa), "n_resolver": len(resolver),
           "categories": {c: sum(q.category == c for q in questions) for c in {q.category for q in questions}},
           "coverage_notes": notes, "retrieval": {}, "generation": None,
           "resolver": {"summary": summarise_resolver(resolver), "rows": resolver}}
    # (label, reranker) passes: without first, so the baseline is never measured through a
    # half-warmed model or a changed candidate depth.
    passes = [("", None)] + ([("+rerank", rerank)] if rerank is not None else [])
    res["reranked"] = rerank is not None
    previous = index.reranker
    try:
        for suffix, reranker in passes:
            index.reranker = reranker
            for m in modes:
                rows = retrieval_eval(index, store, today, qa, m, k, label=m + suffix)
                res["retrieval"][m + suffix] = {"summary": summarise_retrieval(rows), "rows": rows}
        if llm is not None:
            res["generation"] = {}
            for suffix, reranker in passes:
                index.reranker = reranker
                for m in modes:
                    rows = generation_eval(llm, index, store, today, qa, m, trace_dir,
                                           judge_llm, judge_model, question_timeout,
                                           label=m + suffix)
                    res["generation"][m + suffix] = {"summary": summarise_generation(rows), "rows": rows}
    finally:
        index.reranker = previous
    res["failure_cases"] = failure_cases(res["retrieval"], res["generation"], resolver)
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kanon-eval")
    p.add_argument("--questions", type=Path, default=EVALS_DIR / "questions.yaml")
    p.add_argument("--out", type=Path, default=EVALS_DIR)
    p.add_argument("--k", type=int, default=8)   # same k the agent's tools use (tools.make_tools)
    p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--judge", action="store_true", help="append Tier C annotations to traces")
    p.add_argument("--rerank", nargs="?", const="", default=None, metavar="HF_MODEL",
                   help="also measure every mode with the cross-encoder reranker "
                        "(default KANON_RERANK_MODEL, else BAAI/bge-reranker-v2-m3); "
                        "doubles the run, and with an LLM doubles its cost")
    # Measured on this corpus: a question takes roughly 7-23 s (three sequential reasoning-model
    # calls). 60 s leaves headroom over the slowest seen and still catches a hung request.
    p.add_argument("--question-timeout", type=float,
                   default=float(os.environ.get("KANON_QUESTION_TIMEOUT", 60)),
                   help="seconds allowed per question per mode (0 = no limit); "
                        "a timed-out question is recorded, reported, and the run moves on")
    args = p.parse_args(argv)

    from kanon.config import load_settings
    from kanon.llm import chat_model, embeddings
    from kanon.retrieval import open_chroma
    s = load_settings()
    store = AuthorityStore(s.db_path)
    index = CorpusIndex(open_chroma(s), embeddings(s), store)
    if index.hot.count() + index.cold.count() == 0:
        print("index is empty — run `kanon load` and `kanon ingest` first", file=sys.stderr)
        return 1
    rerank = None
    if args.rerank is not None:
        from kanon.rerank import DEFAULT_MODEL, CrossEncoderReranker
        rerank = CrossEncoderReranker(args.rerank or s.rerank_model or DEFAULT_MODEL)
        print(f"reranker: {rerank.model} (first search downloads the weights)", file=sys.stderr)
    llm = None if args.retrieval_only else chat_model(s)
    judge_llm = chat_model(s, role="judge") if args.judge and llm else None
    try:
        res = run(index, store, args.questions, args.k, tuple(args.modes), llm,
                  s.trace_dir / "eval", judge_llm, s.judge_model, args.question_timeout, rerank)
    except BudgetExceeded as exc:
        # Deliberate stop, not a crash: the budget exists so a run cannot quietly spend an
        # account. Results are not written, because a partial sweep is not a comparison.
        print(f"\nSTOPPED: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    (args.out / "report.md").write_text(render_report(res))
    for m, g in (res["generation"] or {}).items():
        print(f"{m:5s}  {pair(g['summary'])}")
    # Say what the run cost in requests. Chat calls dominate the bill; embeddings are cents.
    print(f"{used()} model requests (chat + embeddings) in this run")
    print(f"wrote {args.out / 'report.md'} and {args.out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
