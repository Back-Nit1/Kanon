"""Sliding window + request budget. Fake clock and fake sleep: the tests must not actually wait."""

import pytest

from kanon.ratelimit import BudgetExceeded, SlidingWindow


class Clock:
    """Monotonic fake. `sleep` advances it, exactly as a real sleep would."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0          # never sleep a negative/zero interval
        self.now += seconds


def window(limit=3, window=60.0, budget=0):
    c = Clock()
    return SlidingWindow(limit=limit, window=window, budget=budget, clock=c, sleep=c.sleep), c


def test_requests_under_the_limit_never_wait():
    w, c = window(limit=3)
    for _ in range(3):
        assert w.acquire() is True
    assert c.now == 1000.0 and w.used == 3


def test_the_fourth_request_waits_until_the_first_leaves_the_window():
    w, c = window(limit=3, window=60.0)
    for _ in range(3):
        w.acquire()
    c.now += 10                      # 10 s into the window
    w.acquire()
    # It may only proceed once the oldest of the three is 60 s old: 1000 + 60.
    assert c.now == pytest.approx(1060.0)
    assert w.used == 4


def test_the_window_slides_rather_than_resetting_in_blocks():
    """A fixed *bucket* would let 3 more through the instant the minute ticks over. A sliding
    window admits one request per expiring request, which is what providers actually count."""
    w, c = window(limit=3, window=60.0)
    for _ in range(3):
        w.acquire()
        c.now += 5                   # hits at 1000, 1005, 1010
    c.now = 1061                     # only the first hit has aged out
    w.acquire()                      # free: two in the window
    assert c.now == 1061
    w.acquire()                      # must wait for the 1005 hit to reach 60 s
    assert c.now == pytest.approx(1065.0)


def test_non_blocking_acquire_reports_full_without_recording():
    w, c = window(limit=1)
    assert w.acquire() is True
    assert w.acquire(blocking=False) is False
    assert w.used == 1               # a refused request is not a used request
    assert c.now == 1000.0           # and it did not wait


def test_budget_stops_the_process_and_the_message_says_how_to_continue():
    w, _ = window(limit=100, budget=2)
    w.acquire()
    w.acquire()
    with pytest.raises(BudgetExceeded, match="KANON_MAX_REQUESTS"):
        w.acquire()


def test_budget_counts_requests_that_had_to_wait():
    w, c = window(limit=1, window=60.0, budget=2)
    w.acquire()
    w.acquire()                      # waited, still counts
    assert c.now == pytest.approx(1060.0)
    with pytest.raises(BudgetExceeded):
        w.acquire()


def test_limiter_is_configured_from_the_environment(monkeypatch):
    from kanon import ratelimit

    monkeypatch.setenv("KANON_MAX_REQUESTS_PER_MIN", "7")
    monkeypatch.setenv("KANON_MAX_REQUESTS", "9")
    ratelimit.reset()
    lim = ratelimit.limiter()
    assert (lim.limit, lim.budget) == (7, 9)
    assert ratelimit.limiter() is lim          # built once per process

    monkeypatch.setenv("KANON_MAX_REQUESTS_PER_MIN", "0")
    monkeypatch.setenv("KANON_MAX_REQUESTS", "0")
    ratelimit.reset()
    assert ratelimit.limiter() is None         # both off = no limiter at all
    ratelimit.reset()


def test_embeddings_are_paced_by_the_same_limiter():
    """Chat models take langchain's rate_limiter hook; Embeddings has none, so it is wrapped.
    Both must share one counter or the budget is not a budget."""
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from kanon.llm import LimitedEmbeddings

    w, _ = window(limit=100)
    emb = LimitedEmbeddings(DeterministicFakeEmbedding(size=8), w)
    emb.embed_query("one")
    emb.embed_documents(["a", "b", "c"])       # one batch = one request
    assert w.used == 2
