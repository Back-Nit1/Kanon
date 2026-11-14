"""All runtime configuration comes from the environment. Nothing model-specific is hardcoded
elsewhere in the package — swap a model by changing one env var."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

# Known output dimensions, used by the Tier A dimensionality assertion. If you point
# EMBEDDING_MODEL at something not listed here, set EMBEDDING_DIM explicitly; the check
# refuses to guess.
KNOWN_EMBEDDING_DIMS = {
    "openai:text-embedding-3-small": 1536,
    "openai:text-embedding-3-large": 3072,
}


@dataclass(frozen=True)
class Settings:
    # Model strings are "provider:model" so langchain's init_chat_model / init_embeddings
    # can route them. Pointing JUDGE_MODEL at "anthropic:..." is the one-line change that
    # makes the judge cross-family independent (see README known limitations).
    rag_model: str
    judge_model: str
    embedding_model: str
    embedding_dim: int
    data_dir: Path
    db_path: Path
    chroma_dir: Path
    trace_dir: Path


def _model(var: str, default: str) -> str:
    value = os.environ.get(var, default)
    # Accept the bare names from the spec (e.g. "gpt-5.6-sol") and assume OpenAI.
    return value if ":" in value else f"openai:{value}"


def load_settings() -> Settings:
    embedding_model = _model("EMBEDDING_MODEL", "text-embedding-3-small")
    dim = os.environ.get("EMBEDDING_DIM") or KNOWN_EMBEDDING_DIMS.get(embedding_model)
    if dim is None:
        raise ValueError(f"Unknown dimension for {embedding_model}; set EMBEDDING_DIM.")
    data_dir = Path(os.environ.get("KANON_DATA_DIR", ROOT / "data"))
    return Settings(
        rag_model=_model("RAG_MODEL", "gpt-5.6-sol"),
        judge_model=_model("JUDGE_MODEL", "gpt-5.6-sol"),
        embedding_model=embedding_model,
        embedding_dim=int(dim),
        data_dir=data_dir,
        db_path=Path(os.environ.get("KANON_DB", ROOT / "authority.db")),
        chroma_dir=Path(os.environ.get("KANON_CHROMA_DIR", ROOT / ".chroma")),
        trace_dir=Path(os.environ.get("KANON_TRACE_DIR", ROOT / "traces")),
    )
