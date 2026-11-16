"""Thin provider-agnostic wrapper.

langchain's init_chat_model / init_embeddings already route "provider:model" strings to the
right client, so the wrapper is just "read the env var, call init". Writing our own provider
switch would be an interface with one implementation.

Every paid request in this project goes through one of the two functions below, which is why the
shared rate limiter and request budget are attached here (see kanon/ratelimit.py) rather than at
each call site. The chat models take langchain's own `rate_limiter` hook; `Embeddings` has no
such hook, so the embedder is wrapped.
"""

import os

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from kanon.config import Settings
from kanon.ratelimit import limiter


class LimitedEmbeddings(Embeddings):
    """`Embeddings` has no rate_limiter hook, so pass each call through the shared limiter.
    One ingest batch or one query is one request, which is how the provider counts it."""

    def __init__(self, inner: Embeddings, rate_limiter):
        self.inner, self.rate_limiter = inner, rate_limiter

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.rate_limiter.acquire()
        return self.inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        self.rate_limiter.acquire()
        return self.inner.embed_query(text)


# Reasoning effort, weakest to strongest. Reasoning tokens are billed as output and are the
# single largest per-call cost on this stack, so the setting is capped, not merely defaulted.
EFFORTS = ("minimal", "low", "medium", "high")
CAP = "medium"
CAPPED_MODELS = ("sol",)    # the Sol family: medium is this project's ceiling, by decision


def reasoning_effort(model: str) -> str:
    """KANON_REASONING_EFFORT, clamped to `CAP` for the Sol models.

    Medium is the project default and, for Sol, also the maximum: one eval sweep is ~600 chat
    calls, so raising effort multiplies invisible reasoning tokens across every one of them.
    A request for 'high' on a Sol model is lowered to medium rather than silently honoured;
    a weaker setting ('low', 'minimal') is left alone, since nothing here needs protecting
    from spending less.
    """
    want = os.environ.get("KANON_REASONING_EFFORT", CAP)
    if want not in EFFORTS:
        raise ValueError(f"KANON_REASONING_EFFORT={want!r}; expected one of {EFFORTS}")
    if any(name in model for name in CAPPED_MODELS) and EFFORTS.index(want) > EFFORTS.index(CAP):
        return CAP
    return want


def chat_model(settings: Settings, role: str = "rag") -> BaseChatModel:
    """role: 'rag' (generation path) or 'judge' (Tier C, advisory)."""
    model = settings.judge_model if role == "judge" else settings.rag_model
    # Temperature is opt-in (KANON_TEMPERATURE): reasoning models such as the GPT-5 family reject
    # anything but the default. Repeatability of the *authority* decision doesn't depend on it —
    # the resolver and gate are deterministic; only answer wording varies run to run.
    temperature = os.environ.get("KANON_TEMPERATURE")
    kwargs = {"temperature": float(temperature)} if temperature else {}
    # Keep reasoning deliberate and predictable for OpenAI reasoning models. Do not send this
    # provider-specific option to an independently configured non-OpenAI judge.
    if model.startswith("openai:"):
        # Billed reasoning tokens you never see in the output: the main cost driver per call.
        kwargs["reasoning_effort"] = reasoning_effort(model)
    if (rate_limiter := limiter()) is not None:
        kwargs["rate_limiter"] = rate_limiter
    return init_chat_model(model, **kwargs)


def embeddings(settings: Settings) -> Embeddings:
    inner = init_embeddings(settings.embedding_model)
    rate_limiter = limiter()
    return LimitedEmbeddings(inner, rate_limiter) if rate_limiter is not None else inner
