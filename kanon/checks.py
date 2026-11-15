"""Tier A — pipeline integrity. Deterministic, no LLM.

Using an LLM to check whether embeddings were created is indefensible; these are assertions.
On failure: raise, surface, halt. Never self-repair — a pipeline that patches its own index
hides exactly the corruption these checks exist to catch.
"""

from kanon.ingest import REQUIRED_KEYS, Chunk
from kanon.retrieval import CorpusIndex, is_record


class PipelineIntegrityError(RuntimeError):
    pass


def _fail(msg: str) -> None:
    raise PipelineIntegrityError(msg)


def check_chunks(chunks: list[Chunk]) -> None:
    if not chunks:
        _fail("ingest produced zero chunks")
    # Field presence is enforced by the Chunk model at construction; here we check values.
    for c in chunks:
        if not isinstance(c.page, int) or c.page < 1:
            _fail(f"{c.id}: no valid page ({c.page!r}); a chunk without a page is not citable")
        for key in ("doc_id", "revision", "status", "kind", "effective_from"):
            # Records are exempt from effective_from: an event or notice doesn't "govern" over
            # an interval. Requirements must be placeable on the timeline, or the resolver
            # can't judge them and they must not enter the corpus silently.
            if key == "effective_from" and is_record(c.kind):
                continue
            if getattr(c, key) in (None, ""):
                _fail(f"{c.id}: empty required field '{key}'")


def check_index(index: CorpusIndex, chunk_ids: set[str], expected_dim: int) -> None:
    """chunk_ids: what the pipeline believes it wrote (the Postgres chunks table, after a queued
    ingest). The vector store must hold exactly that many."""
    hot, cold = index.hot.get(include=["metadatas", "embeddings"]), index.cold.get(include=["metadatas", "embeddings"])

    stored = len(hot["ids"]) + len(cold["ids"])
    if stored != len(chunk_ids):
        _fail(f"embedding count {stored} != chunk count {len(chunk_ids)}")

    for data in (hot, cold):
        for cid, vec in zip(data["ids"], data["embeddings"]):
            if len(vec) != expected_dim:
                _fail(f"{cid}: embedding dim {len(vec)} != configured {expected_dim}")
        for cid, meta in zip(data["ids"], data["metadatas"]):
            missing = (REQUIRED_KEYS - {"id", "text"}) - set(meta)
            if missing:
                _fail(f"{cid}: stored without metadata {sorted(missing)}")
            if not meta.get("page"):
                _fail(f"{cid}: stored without page")

    # Assert over every row, don't sample: one superseded chunk in hot is the whole failure mode.
    leaked = [cid for cid, m in zip(hot["ids"], hot["metadatas"]) if m["status"] != "active"]
    if leaked:
        _fail(f"hot collection holds {len(leaked)} non-active chunks, e.g. {leaked[:3]}")


def check_round_trip(index: CorpusIndex, chunk: Chunk) -> None:
    """A known chunk must be retrievable by its own text (mode 'none' searches both tiers)."""
    hits = (index.search_precedent(chunk.text, k=5) if is_record(chunk.kind)
            else index.search(chunk.text, k=5, mode="none"))
    if chunk.id not in [h.id for h in hits]:
        _fail(f"round-trip failed: {chunk.id} not in top-5 for its own text")


def run_all(index: CorpusIndex, chunks: list[Chunk], expected_dim: int) -> None:
    check_chunks(chunks)
    check_index(index, {c.id for c in chunks}, expected_dim)
    check_round_trip(index, chunks[0])
