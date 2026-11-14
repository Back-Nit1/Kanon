"""Thin provider-agnostic wrapper.

langchain's init_chat_model / init_embeddings already route "provider:model" strings to the
right client, so the wrapper is just "read the env var, call init". Writing our own provider
switch would be an interface with one implementation.
"""

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from kanon.config import Settings


def chat_model(settings: Settings, role: str = "rag") -> BaseChatModel:
    """role: 'rag' (generation path) or 'judge' (Tier C, advisory)."""
    model = settings.judge_model if role == "judge" else settings.rag_model
    # temperature 0: classification and citation extraction should be repeatable across runs
    # of the frozen question set. Some reasoning models reject the param; drop it if so.
    return init_chat_model(model, temperature=0)


def embeddings(settings: Settings) -> Embeddings:
    return init_embeddings(settings.embedding_model)
