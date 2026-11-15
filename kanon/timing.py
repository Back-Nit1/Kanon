"""Stage timing: one JSON line per timed span, so a developer can see where a run spends time.

    with span("llm.draft"): ...

Records go to traces/timing.jsonl (KANON_TIMING_LOG overrides). `kanon-dashboard` reads it.
Rejected: OpenTelemetry/LangSmith — a collector or hosted service for a single-process eval
harness. An append-only JSONL file is enough and can be read by `cat`.
Not thread-safe by design: the eval runs questions one at a time, so one module-level context
(which mode / question is running) is accurate. Revisit if evals are ever parallelised.
"""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from kanon.config import ROOT

LOG = Path(os.environ.get("KANON_TIMING_LOG", ROOT / "traces" / "timing.jsonl"))
_context: dict = {}


def set_context(**kw) -> None:
    """Tag every following span (e.g. mode, qid). Call with no args to clear."""
    _context.clear()
    _context.update(kw)


@contextmanager
def span(stage: str, **extra):
    start = time.perf_counter()
    try:
        yield
    finally:
        rec = {"ts": round(time.time(), 3), "stage": stage,
               "ms": round((time.perf_counter() - start) * 1000, 1), **_context, **extra}
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass   # timing must never break a run
