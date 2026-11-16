from types import SimpleNamespace

import pytest

from kanon import llm, ratelimit


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    """The limiter is a process singleton; rebuild it from each test's environment."""
    ratelimit.reset()
    yield
    ratelimit.reset()


def capture(monkeypatch) -> dict:
    captured = {}
    monkeypatch.setattr(llm, "init_chat_model",
                        lambda model, **kwargs: captured.update(model=model, **kwargs))
    return captured


def test_openai_chat_model_defaults_to_medium_reasoning(monkeypatch):
    monkeypatch.delenv("KANON_REASONING_EFFORT", raising=False)
    captured = capture(monkeypatch)

    llm.chat_model(SimpleNamespace(rag_model="openai:gpt-5.6-sol", judge_model="unused"))

    assert captured["model"] == "openai:gpt-5.6-sol"
    assert captured["reasoning_effort"] == "medium"


def test_non_openai_judge_does_not_receive_openai_reasoning_effort(monkeypatch):
    monkeypatch.setenv("KANON_REASONING_EFFORT", "medium")
    captured = capture(monkeypatch)

    llm.chat_model(SimpleNamespace(rag_model="unused", judge_model="anthropic:claude-example"),
                   role="judge")

    assert "reasoning_effort" not in captured and captured["model"] == "anthropic:claude-example"


def test_sol_reasoning_effort_is_capped_at_medium(monkeypatch):
    """Reasoning tokens are billed output, and an eval sweep is ~600 chat calls: 'high' on a Sol
    model is lowered rather than honoured."""
    monkeypatch.setenv("KANON_REASONING_EFFORT", "high")
    assert llm.reasoning_effort("openai:gpt-5.6-sol") == "medium"
    captured = capture(monkeypatch)
    llm.chat_model(SimpleNamespace(rag_model="openai:gpt-5.6-sol", judge_model="unused"))
    assert captured["reasoning_effort"] == "medium"


def test_the_cap_only_lowers_and_only_for_sol(monkeypatch):
    monkeypatch.setenv("KANON_REASONING_EFFORT", "low")
    assert llm.reasoning_effort("openai:gpt-5.6-sol") == "low"      # spending less is never capped
    monkeypatch.setenv("KANON_REASONING_EFFORT", "high")
    assert llm.reasoning_effort("openai:o-example") == "high"       # cap is a Sol-family decision


def test_an_unknown_reasoning_effort_is_rejected(monkeypatch):
    monkeypatch.setenv("KANON_REASONING_EFFORT", "maximum")
    with pytest.raises(ValueError, match="KANON_REASONING_EFFORT"):
        llm.reasoning_effort("openai:gpt-5.6-sol")


def test_chat_model_and_embeddings_share_one_limiter(monkeypatch):
    monkeypatch.setenv("KANON_MAX_REQUESTS_PER_MIN", "30")
    monkeypatch.setenv("KANON_MAX_REQUESTS", "100")
    ratelimit.reset()
    captured = capture(monkeypatch)
    monkeypatch.setattr(llm, "init_embeddings", lambda model: object())

    llm.chat_model(SimpleNamespace(rag_model="openai:gpt-5.6-sol", judge_model="unused"))
    emb = llm.embeddings(SimpleNamespace(embedding_model="openai:text-embedding-3-small"))

    assert captured["rate_limiter"] is emb.rate_limiter is ratelimit.limiter()


def test_limits_turned_off_means_no_wrapper(monkeypatch):
    monkeypatch.setenv("KANON_MAX_REQUESTS_PER_MIN", "0")
    monkeypatch.setenv("KANON_MAX_REQUESTS", "0")
    ratelimit.reset()
    captured = capture(monkeypatch)
    inner = object()
    monkeypatch.setattr(llm, "init_embeddings", lambda model: inner)

    llm.chat_model(SimpleNamespace(rag_model="openai:gpt-5.6-sol", judge_model="unused"))

    assert "rate_limiter" not in captured
    assert llm.embeddings(SimpleNamespace(embedding_model="openai:x")) is inner
