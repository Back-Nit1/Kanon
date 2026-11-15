"""CLI: the only UI. Takes a question, prints the answer, the gate decision, and the trace path.

  kanon load                         manifest + supersession labels -> authority.db
  kanon ingest                       queue manifest -> work the queue -> Tier A checks
  kanon worker                       one more queue worker (run several in parallel)
  kanon dlq [--requeue KEY]          dead letter report: failed documents and why
  kanon resolve RG-1.187 --as-of 2019-06-01
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
    sub.add_parser("worker")
    d = sub.add_parser("dlq")
    d.add_argument("--requeue", metavar="IDEM_KEY")
    d.add_argument("--failing", action="store_true",
                   help="Rank documents by accumulated failures across ingest jobs.")
    r = sub.add_parser("resolve")
    r.add_argument("doc_id")
    r.add_argument("--as-of", type=date.fromisoformat, required=True)
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
        return 0

    if args.cmd == "dlq":
        from kanon import queue
        conn = queue.connect(s.pg_dsn)
        if args.requeue:
            ok = queue.requeue(conn, args.requeue)
            print("requeued; run `kanon worker`" if ok else f"no dead job with key {args.requeue}")
            return 0 if ok else 1
        if args.failing:
            print(queue.failing_documents_report(conn))
            return 0
        print(queue.dead_letter_report(conn))
        return 0

    from kanon.llm import embeddings
    from kanon.retrieval import CorpusIndex, open_chroma
    index = CorpusIndex(open_chroma(s), embeddings(s), store, expected_dim=s.embedding_dim)

    if args.cmd in ("ingest", "worker"):
        from docling.chunking import HybridChunker
        from kanon import queue
        from kanon.ingest import Chunk, make_converter
        conn = queue.connect(s.pg_dsn)
        if args.cmd == "ingest":
            print(f"queued {queue.enqueue_manifest(conn, s.data_dir, store)} new job(s)")
        queue.run_worker(conn, s.data_dir, store, index, make_converter(), HybridChunker())
        if args.cmd == "worker":
            return 0
        n = queue.counts(conn)
        ids = queue.chunk_ids(conn)
        checks.check_index(index, ids, s.embedding_dim)
        if not ids:
            raise checks.PipelineIntegrityError("ingest produced zero chunks")
        for col in (index.hot, index.cold):     # round-trip one stored chunk by its own text
            got = col.get(ids=[min(ids)], include=["documents", "metadatas"])
            if got["ids"]:
                checks.check_round_trip(index, Chunk.from_chroma(got["ids"][0], got["documents"][0], got["metadatas"][0]))
        print(f"jobs {n}; {len(ids)} chunks: hot={index.hot.count()} cold={index.cold.count()}; Tier A green")
        if n.get("dead"):
            print(f"{n['dead']} document(s) failed {queue.MAX_ATTEMPTS}x and are NOT searchable: run `kanon dlq`")
            return 1
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
        print(f"[trace: {path}]")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
