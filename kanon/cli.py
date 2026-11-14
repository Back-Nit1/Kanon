"""CLI: the only UI. Takes a question, prints the answer, the gate decision, and the trace path.

  kanon load                         manifest + supersession labels -> authority.db
  kanon ingest                       Docling parse -> chunks -> embed -> hot/cold + Tier A checks
  kanon resolve RG-1.187 --as-of 2019-06-01
  kanon ask "question" [--mode graph|flag|none] [--today YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date

import chromadb

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

    from kanon.llm import embeddings
    from kanon.retrieval import CorpusIndex
    index = CorpusIndex(chromadb.PersistentClient(path=str(s.chroma_dir)), embeddings(s), store)

    if args.cmd == "ingest":
        from kanon.ingest import chunk_corpus
        chunks = chunk_corpus(s.data_dir, store)
        checks.check_chunks(chunks)          # fail before paying for embeddings
        index.add(chunks)
        checks.run_all(index, chunks, s.embedding_dim)
        print(f"ingested {len(chunks)} chunks: hot={index.hot.count()} cold={index.cold.count()}; Tier A green")
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
        print(state["final"])
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
