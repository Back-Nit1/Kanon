import json

from kanon import timing
from kanon.dashboard import load_timing


def test_span_writes_one_tagged_line(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, "LOG", tmp_path / "t.jsonl")
    timing.set_context(mode="graph", qid="q01")
    with timing.span("llm.draft", context_chunks=3):
        pass
    timing.set_context()
    rec = json.loads((tmp_path / "t.jsonl").read_text())
    assert rec["stage"] == "llm.draft" and rec["mode"] == "graph" and rec["qid"] == "q01"
    assert rec["context_chunks"] == 3 and rec["ms"] >= 0


def test_loader_skips_half_written_line(tmp_path):
    log = tmp_path / "t.jsonl"
    log.write_text('{"stage": "a", "ms": 1}\n{"stage": "b", "m')
    assert [r["stage"] for r in load_timing(log)] == ["a"]


def test_deadline_interrupts_a_blocking_call():
    import time

    import pytest
    start = time.perf_counter()
    with pytest.raises(timing.QuestionTimeout):
        with timing.deadline(0.2):
            time.sleep(5)
    assert time.perf_counter() - start < 2


def test_no_deadline_is_a_noop():
    with timing.deadline(0):
        pass


def test_dlq_endpoint_reports_unreachable_db_not_empty(monkeypatch):
    # empty queue and unreachable DB must look different on the dashboard
    from kanon import dashboard, queue
    monkeypatch.setattr(queue, "connect", lambda dsn: (_ for _ in ()).throw(OSError("down")))
    d = dashboard.load_dlq()
    assert d["ok"] is False and "OSError" in d["error"]
