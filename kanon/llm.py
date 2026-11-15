"""Thin provider-agnostic wrapper.

langchain's init_chat_model / init_embeddings already route "provider:model" strings to the
right client, so the wrapper is just "read the env var, call init". Writing our own provider
switch would be an interface with one implementation.
"""

import os

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from kanon.config import Settings


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
        kwargs["reasoning_effort"] = os.environ.get("KANON_REASONING_EFFORT", "medium")
    return init_chat_model(model, **kwargs)


def embeddings(settings: Settings) -> Embeddings:
    return init_embeddings(settings.embedding_model)
