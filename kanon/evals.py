"""Evaluation harness: the same pinned question set through modes none / flag / graph.

    uv run kanon-eval                    # retrieval metrics + full agent runs, all three modes
    uv run kanon-eval --retrieval-only   # no chat model needed (still needs the embedder)
    uv run kanon-eval --judge            # also append Tier C annotations to every trace

Two levels, kept separate on purpose:
  - Retrieval (CorpusIndex.search per mode, no LLM): recall@k, MRR, and superseded-chunk rate —
    the share of retrieved chunks whose document did not govern on the question's as_of.
  - Generation (the full agent): superseded-citation rate, ALWAYS paired with answer rate. A
    system that abstains on everything scores 0% superseded and is useless; the pair is the
    metric. `pair()` is the only function that formats either number, so neither can be printed
    alone.

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
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from kanon.ingest import Chunk
from kanon.retrieval import CorpusIndex, is_record
from kanon.store import AuthorityStore

MODES = ("none", "flag", "graph")
# The three cases where a status flag is structurally blind and the bi-temporal graph is not.
THESIS_CATEGORIES = {"partial": "partial supersession",
                     "cross_family": "cross-family supersession",
                     "effective_before_issue": "effective date precedes issue date"}
EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


class Question(BaseModel):
    id: str
    category: str
    query_kind: Literal["current", "time_anchored"]
    as_of: date | None = None       # required for time_anchored; must be absent for current
    question: str
    # "DOC@REV" keys (same form as Chunk.doc_key) that govern the answer on as_of.
    # Empty = no governing document exists in the corpus: the correct behaviour is to abstain.
    expected: list[str]

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


# Hook for the optional "mode none with/without reranker" run (KANON.md, Reranking). Not
# implemented: BAAI/bge-reranker-v2-m3 needs a 0.6B local model and a new dependency, and the
# result doesn't touch the thesis. If added: pass a function (query, chunks) -> reordered chunks.
# Never compare raw reranker scores across models (BGE sigmoid vs Cohere vs ColBERT MaxSim live on
# different scales) — compare only the metrics below.
Reranker = Callable[[str, list[Chunk]], list[Chunk]]


def retrieval_eval(index: CorpusIndex, store: AuthorityStore, today: date,
                   questions: list[Question], mode: str, k: int,
                   rerank: Reranker | None = None) -> list[dict]:
    rows = []
    for q in questions:
        as_of = q.authority_date(today)
        chunks = index.search(q.question, k if rerank is None else 3 * k, mode, as_of)
        if rerank is not None:
            chunks = rerank(q.question, chunks)[:k]
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
            "answer_rate": answered / n if n else 0.0,
            # Over ANSWERED questions. With nothing answered it is 0.0 — meaningless on its own,
            # which is exactly why it is only ever reported next to answer_rate.
            "superseded_citation_rate": sum(r["superseded"] for r in rows) / answered if answered else 0.0,
            "cites_expected_rate": sum(r["cites_expected"] for r in rows) / answered if answered else 0.0}


def pair(s: dict) -> str:
    """The one place either half of the primary metric is formatted. Never split it."""
    return (f"superseded-citation rate {s['superseded_citation_rate']:.0%} "
            f"@ answer rate {s['answer_rate']:.0%} ({s['answered']}/{s['questions']} answered)")


def generation_eval(llm, index: CorpusIndex, store: AuthorityStore, today: date,
                    questions: list[Question], mode: str, trace_dir: Path,
                    judge_llm=None, judge_model: str = "") -> list[dict]:
    from kanon.agent import build_agent
    from kanon.trace import TraceRecorder
    agent = build_agent(llm, index, store, mode)
    rows = []
    for q in questions:
        recorder = TraceRecorder()
        state = agent.invoke({"question": q.question, "mode": mode, "today": today},
                             config={"callbacks": [recorder]})
        # One directory per question: TraceRecorder names files by the second, so two fast
        # questions in one directory would overwrite each other.
        path = recorder.write(trace_dir / mode / q.id, state)
        if judge_llm is not None:
            from kanon.judge import append_to_trace, review
            append_to_trace(path, review(judge_llm, state), judge_model)
        rows.append({**generation_row(q, state, store, q.authority_date(today)), "trace": str(path)})
    return rows


# ---- named failure cases ------------------------------------------------------------------

def failure_cases(retrieval: dict, generation: dict | None) -> list[dict]:
    """Questions where `flag` fails and `graph` succeeds. Computed, never asserted: if this list
    is empty the report says so."""
    cases = []
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
            if f is None:
                continue
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
         f"{res['n_questions']} questions, pinned today = {res['today']}, k = {res['k']}",
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

    L += ["## Retrieval (no LLM)", "",
          "| mode | recall@k | MRR | superseded-chunk rate | retrieved chunks |", "|---|---|---|---|---|"]
    for m, r in res["retrieval"].items():
        s = r["summary"]
        L.append(f"| {m} | {s['recall_at_k']:.2f} | {s['mrr']:.2f} | {s['superseded_chunk_rate']:.0%} "
                 f"| {s['retrieved_chunks']} |")
    L += ["", "Recall@k and MRR are over the questions with a non-empty expected set; "
          "superseded-chunk rate is pooled over every retrieved chunk.", ""]

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
          "as_of — never by a model. Operating-experience citations are exempt (they record events).",
          "- Reranker run (mode none ± BAAI/bge-reranker-v2-m3): not run; hook only (`Reranker`).",
          "- Tier C judge annotations, when enabled, are advisory and live in the traces; they "
          "do not feed any metric here. Judge shares a provider with the RAG model by default.", ""]
    return "\n".join(L)


def run(index: CorpusIndex, store: AuthorityStore, questions_file: Path, k: int,
        modes: tuple[str, ...] = MODES, llm=None, trace_dir: Path | None = None,
        judge_llm=None, judge_model: str = "") -> dict:
    today, questions, sha, notes = load_questions(questions_file)
    problems = check_questions(store, today, questions)
    if problems:   # halt: grading against an underivable expected answer is worse than no grade
        raise ValueError("question set does not match the authority store:\n  " + "\n  ".join(problems))
    res = {"questions_file": str(questions_file), "questions_sha256": sha, "today": str(today),
           "k": k, "n_questions": len(questions),
           "categories": {c: sum(q.category == c for q in questions) for c in {q.category for q in questions}},
           "coverage_notes": notes, "retrieval": {}, "generation": None}
    for m in modes:
        rows = retrieval_eval(index, store, today, questions, m, k)
        res["retrieval"][m] = {"summary": summarise_retrieval(rows), "rows": rows}
    if llm is not None:
        res["generation"] = {}
        for m in modes:
            rows = generation_eval(llm, index, store, today, questions, m, trace_dir,
                                   judge_llm, judge_model)
            res["generation"][m] = {"summary": summarise_generation(rows), "rows": rows}
    res["failure_cases"] = failure_cases(res["retrieval"], res["generation"])
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kanon-eval")
    p.add_argument("--questions", type=Path, default=EVALS_DIR / "questions.yaml")
    p.add_argument("--out", type=Path, default=EVALS_DIR)
    p.add_argument("--k", type=int, default=8)   # same k the agent's tools use (tools.make_tools)
    p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--judge", action="store_true", help="append Tier C annotations to traces")
    args = p.parse_args(argv)

    import chromadb
    from kanon.config import load_settings
    from kanon.llm import chat_model, embeddings
    s = load_settings()
    store = AuthorityStore(s.db_path)
    index = CorpusIndex(chromadb.PersistentClient(path=str(s.chroma_dir)), embeddings(s), store)
    if index.hot.count() + index.cold.count() == 0:
        print("index is empty — run `kanon load` and `kanon ingest` first", file=sys.stderr)
        return 1
    llm = None if args.retrieval_only else chat_model(s)
    judge_llm = chat_model(s, role="judge") if args.judge and llm else None
    res = run(index, store, args.questions, args.k, tuple(args.modes), llm,
              s.trace_dir / "eval", judge_llm, s.judge_model)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    (args.out / "report.md").write_text(render_report(res))
    for m, g in (res["generation"] or {}).items():
        print(f"{m:5s}  {pair(g['summary'])}")
    print(f"wrote {args.out / 'report.md'} and {args.out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
