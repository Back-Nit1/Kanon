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
import signal
import threading
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


class QuestionTimeout(BaseException):
    """Raised inside a run when its deadline passes. BaseException on purpose: library code that
    does `except Exception` (retry loops, callbacks) must not swallow the deadline."""


@contextmanager
def deadline(seconds: float | None):
    """Abort the enclosed block after `seconds` of wall time (0/None = no limit).

    SIGALRM interrupts a blocking network call (a hung model request) where a thread-based
    timeout could only stop waiting and leave the call running in the background, still billing.
    Rejected: ThreadPoolExecutor + future.result(timeout) for exactly that reason; per-request
    client timeouts alone, because a question makes several calls and each could be just under
    the limit. Needs the main thread (signals); elsewhere it is a no-op, never an error.
    """
    if not seconds or threading.current_thread() is not threading.main_thread():
        yield
        return

    def fire(*_):
        raise QuestionTimeout(seconds)

    old = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
