"""Cross-encoder reranking — the stage metadata pre-filtering exists to make affordable.

Where it sits: `CorpusIndex._hybrid` narrows the candidate universe by metadata *before* either
ranker runs (authority mode, requirement-vs-record kind, plant/unit/docket, `since`), so the
50-per-retriever budget is only ever spent on eligible chunks. RRF fuses those lists into
RERANK_CANDIDATES, and this cross-encoder re-scores each (query, chunk) pair jointly to pick the
final k that generation cites. Bi-encoder for recall, cross-encoder for precision — and the
expensive model only ever sees text that already passed the filters.

No new dependency: `torch` and `transformers` are already in the tree (docling). The
sentence-transformers `CrossEncoder` wrapper is the same two calls plus a dependency.

Scores are raw logits from the model's single-label head — monotonic in relevance, comparable
within one model and meaningless across models (BGE sigmoid vs Cohere vs ColBERT MaxSim live on
different scales). Rank with them; never threshold on them without calibrating on this corpus.

Off by default. KANON.md (Reranking) is explicit about why: a superseded revision is topically
near-identical to the current one, so better topical ranking may *raise* the superseded-citation
rate. Turn it on via KANON_RERANK_MODEL and A/B it in mode 'none' (`kanon-eval --rerank`) before
trusting it.
"""

from kanon.ingest import Chunk
from kanon.timing import span

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"   # MIT, ~0.6B, self-hostable, the common open default
MAX_LENGTH = 512                            # BGE rerankers are trained at 512; longer is wasted
BATCH = 16


class CrossEncoderReranker:
    """Callable (query, chunks) -> chunks reordered by cross-encoder score, highest first."""

    def __init__(self, model: str = DEFAULT_MODEL, max_length: int = MAX_LENGTH,
                 batch: int = BATCH, device: str | None = None):
        self.model = model
        self.max_length = max_length
        self.batch = batch
        self.device = device
        self._loaded: tuple | None = None

    def _load(self) -> tuple:
        """Lazy: importing torch and pulling ~2 GB of weights must not happen on `import kanon`,
        and a run that never searches must not pay for it."""
        if self._loaded is None:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            with span("rerank.load", model=self.model):
                tokenizer = AutoTokenizer.from_pretrained(self.model)
                model = AutoModelForSequenceClassification.from_pretrained(self.model)
                device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
                self._loaded = (tokenizer, model.to(device).eval(), device)
        return self._loaded

    def scores(self, query: str, texts: list[str]) -> list[float]:
        import torch
        tokenizer, model, device = self._load()
        out: list[float] = []
        for i in range(0, len(texts), self.batch):
            pairs = [[query, t] for t in texts[i:i + self.batch]]
            encoded = tokenizer(pairs, padding=True, truncation=True,
                                max_length=self.max_length, return_tensors="pt").to(device)
            with torch.inference_mode():
                out += model(**encoded).logits.view(-1).float().cpu().tolist()
        return out

    def __call__(self, query: str, chunks: list[Chunk]) -> list[Chunk]:
        if len(chunks) < 2:
            return list(chunks)
        scores = self.scores(query, [c.text for c in chunks])
        # score was the RRF fusion score; past this point it is this model's logit. Both are
        # "retrieval's own ranking score" and neither is ever compared across runs.
        ranked = [c.model_copy(update={"score": s}) for c, s in zip(chunks, scores)]
        return sorted(ranked, key=lambda c: c.score, reverse=True)


def load_reranker(s) -> CrossEncoderReranker | None:
    """None unless KANON_RERANK_MODEL is set. See the module docstring for why off is the default."""
    return CrossEncoderReranker(s.rerank_model) if getattr(s, "rerank_model", "") else None
