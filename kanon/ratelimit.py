"""Request pacing and a hard request budget for every paid model call.

Two different jobs, deliberately kept in one object because they share one counter:

  - **Sliding window** — at most `limit` requests in any trailing `window` seconds. Paces a run
    against provider rate limits and stops a runaway agent loop from firing hundreds of calls in
    a few seconds.
  - **Budget** — at most `budget` requests for the life of the process, then the run stops with
    `BudgetExceeded`. This is the one that protects the bill: pacing changes *when* requests are
    made, never *how many*. A 615-call eval costs the same whether it runs in ten minutes or
    overnight.

Why a sliding window and not a token bucket: `langchain_core.rate_limiters.InMemoryRateLimiter`
is already installed and would have been free, but it is a token bucket — it smooths to an
average rate and lets a refilled bucket burst. A sliding window enforces the limit over every
window, which is how providers actually count, so a run that passes locally does not get a 429.
The cost is space: one timestamp per in-window request, O(limit) floats, accepted on purpose.

What this does NOT do: it counts *requests*, not tokens or dollars. One `draft` call carrying
eight retrieved chunks and medium reasoning effort costs many times a `classify` call. For
spend, requests are a proxy — a good enough one to turn a surprise into a stop.
"""

import os
import threading
import time
from collections import deque

from langchain_core.rate_limiters import BaseRateLimiter


class BudgetExceeded(RuntimeError):
    """The process asked for more model requests than its budget allows."""


class SlidingWindow(BaseRateLimiter):
    """Fixed sliding window + lifetime budget.

    Subclasses langchain's `BaseRateLimiter` rather than merely matching its shape: a chat model
    is a pydantic model and validates the `rate_limiter` field with `isinstance`, so a duck-typed
    limiter is rejected at construction."""

    def __init__(self, limit: int, window: float = 60.0, budget: int = 0,
                 clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.window = window
        self.budget = budget            # 0 = no budget
        self.used = 0                   # every acquire, including ones that waited
        self._clock, self._sleep = clock, sleep
        self._hits: deque[float] = deque()
        # ponytail: one lock held across the wait. The eval runs questions one at a time, so there
        # is no contention to lose; make the wait lock-free if calls are ever parallelised.
        self._lock = threading.Lock()

    def acquire(self, *, blocking: bool = True) -> bool:
        """Wait until the window has room, then record the request. Returns False (without
        recording) if the window is full and `blocking` is False — langchain's contract."""
        with self._lock:
            while True:
                now = self._clock()
                while self._hits and now - self._hits[0] >= self.window:
                    self._hits.popleft()
                if len(self._hits) < self.limit:
                    break
                if not blocking:
                    return False
                self._sleep(self.window - (now - self._hits[0]))
            self._hits.append(now)
            self.used += 1
            if self.budget and self.used > self.budget:
                raise BudgetExceeded(
                    f"{self.used} model requests in this process, budget is {self.budget}. "
                    "Nothing further was sent. Raise KANON_MAX_REQUESTS to continue, or run "
                    "fewer modes/questions (`kanon-eval --modes graph`, `--retrieval-only`).")
            return True

    async def aacquire(self, *, blocking: bool = True) -> bool:
        # No async path in this codebase; present so the langchain protocol is satisfied.
        return self.acquire(blocking=blocking)


# One limiter per process, shared by the chat models and the embedder, because the budget is a
# property of the run and not of any one client.
_LIMITER: SlidingWindow | None = None
_LOADED = False

# A full three-mode eval is ~615 chat calls + ~600 query embeddings, so the default budget stops
# a second accidental full run rather than a legitimate first one. Pacing defaults are generous:
# they exist to catch a loop, not to slow a normal run (which peaks near 25 requests/minute).
DEFAULT_PER_MINUTE = 60
DEFAULT_BUDGET = 1500


def limiter() -> SlidingWindow | None:
    """The process-wide limiter, built once from the environment.
    KANON_MAX_REQUESTS_PER_MIN=0 and KANON_MAX_REQUESTS=0 each disable their half; both 0
    disables it entirely."""
    global _LIMITER, _LOADED
    if not _LOADED:
        per_min = int(os.environ.get("KANON_MAX_REQUESTS_PER_MIN", DEFAULT_PER_MINUTE))
        budget = int(os.environ.get("KANON_MAX_REQUESTS", DEFAULT_BUDGET))
        _LIMITER = (SlidingWindow(limit=per_min or 10**9, budget=budget)
                    if per_min or budget else None)
        _LOADED = True
    return _LIMITER


def reset() -> None:
    """Re-read the environment. For tests; a run builds its limiter once."""
    global _LIMITER, _LOADED
    _LIMITER, _LOADED = None, False


def used() -> int:
    return _LIMITER.used if _LIMITER else 0
