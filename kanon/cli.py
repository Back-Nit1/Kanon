"""CLI: the only UI. Takes a question, prints the answer, the gate decision, and the trace path.

  kanon load                         manifest + supersession labels -> authority.db
  kanon ingest                       manifest -> parse, validate, embed -> Tier A checks
  kanon resolve RG-1.187 --as-of 2019-06-01
  kanon diff 10CFR50.59 --from 2000-06-01 --to 2026-11-15 [--section 50.59]
  kanon ask "question" [--mode graph|flag|none] [--today YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date

from kanon import checks
from kanon.config import load_settings
from kanon.store import AuthorityStore


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kanon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load")
    sub.add_parser("ingest")
    r = sub.add_parser("resolve")
    r.add_argument("doc_id")
    r.add_argument("--as-of", type=date.fromisoformat, required=True)
    df = sub.add_parser("diff")
    df.add_argument("doc_id")
    df.add_argument("--from", dest="from_as_of", type=date.fromisoformat, required=True)
    df.add_argument("--to", dest="to_as_of", type=date.fromisoformat, required=True)
    df.add_argument("--section")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--mode", choices=["none", "flag", "graph"], default="graph")
    a.add_argument("--today", type=date.fromisoformat, default=date.today(),
                   help="Override 'today' so eval runs are reproducible.")
    args = p.parse_args(argv)

    s = load_settings()
    store = AuthorityStore(s.db_path)

    if args.cmd == "load":
        from kanon.supersession import load_labels, load_rg_index
        n = store.load_manifest(s.data_dir / "manifest.jsonl")
        g = load_rg_index(store, s.data_dir / "labels" / "rg_index.jsonl")   # before labels:
        counts = load_labels(store, s.data_dir / "labels" / "supersession_evidence.jsonl")  # pins need dates
        print(f"documents: {n} from manifest + {g} reg guide versions (metadata only)  edges: {counts}")
        return 0

    if args.cmd == "resolve":
        res = store.resolve_authority(args.doc_id, args.as_of)
        print(f"Governing on {args.as_of}: {res.authoritative or 'NOTHING'}")
        history = store.revision_history(args.doc_id)
        if not res.authoritative and history:   # say why nothing governs, don't just say nothing
            first = history[-1]
            print(f"  earliest recorded version: {first.doc_id} Rev {first.revision}, "
                  f"effective {first.effective_from or 'unknown'}")
        elif not history:
            print(f"  {args.doc_id} is not in the authority store")
        for e in res.chain:
            print(f"  chain      {e.describe()}\n             “{e.evidence[:160]}”")
        for e in res.unresolved:
            print(f"  UNRESOLVED {e.describe()}\n             “{e.evidence[:160]}”")
        for k in res.undated:
            print(f"  UNDATED    {k[0]} Rev {k[1]}: no effective_from recorded")
        for k in res.withdrawn_undated:
            print(f"  WITHDRAWN-UNDATED {k[0]} Rev {k[1]}: recorded withdrawn, "
                  "no end date and no dated ending edge")
        return 0

    from kanon.llm import embeddings
    from kanon.rerank import load_reranker
    from kanon.retrieval import CorpusIndex, open_chroma
    index = CorpusIndex(open_chroma(s), embeddings(s), store, expected_dim=s.embedding_dim,
                        reranker=load_reranker(s))

    if args.cmd == "ingest":
        from kanon.ingest import ingest_all
        chunks = ingest_all(s.data_dir, store, index)
        checks.run_all(index, chunks, s.embedding_dim)   # Tier A: count, dim, metadata, hot purity, round-trip
        print(f"{len(chunks)} chunks: hot={index.hot.count()} cold={index.cold.count()}; Tier A green")
        return 0

    if args.cmd == "diff":
        from kanon.diff import diff_as_of, format_diff
        print(format_diff(diff_as_of(index, store, args.doc_id, args.from_as_of,
                                     args.to_as_of, args.section)))
        return 0

    if args.cmd == "ask":
        from kanon.agent import build_agent
        from kanon.llm import chat_model
        from kanon.trace import TraceRecorder
        recorder = TraceRecorder()
        agent = build_agent(chat_model(s), index, store, args.mode)
        state = agent.invoke({"question": args.question, "mode": args.mode, "today": args.today},
                             config={"callbacks": [recorder]})
        path = recorder.write(s.trace_dir, state)
        # One blank line between every line so bullets and sources are easy to scan.
        print("\n\n".join(l for l in state["final"].splitlines() if l.strip()))
        print(f"\n[gate: {state['decision']} | mode: {args.mode} | as_of: {state.get('as_of')}]")
        unenforced = [v for v in state.get("violations", []) if state["decision"] == "answer"]
        if unenforced:
            print(f"[{len(unenforced)} authority violation(s) recorded but not enforced in mode "
                  f"'{args.mode}' — see trace]")
        from kanon.ratelimit import used
        print(f"[trace: {path} | {used()} model requests]")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
