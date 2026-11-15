from types import SimpleNamespace

from kanon import llm


def test_openai_chat_model_defaults_to_medium_reasoning(monkeypatch):
    captured = {}
    monkeypatch.delenv("KANON_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(llm, "init_chat_model", lambda model, **kwargs: captured.update(
        model=model, **kwargs
    ))

    llm.chat_model(SimpleNamespace(rag_model="openai:gpt-5.6-sol", judge_model="unused"))

    assert captured == {"model": "openai:gpt-5.6-sol", "reasoning_effort": "medium"}


def test_non_openai_judge_does_not_receive_openai_reasoning_effort(monkeypatch):
    captured = {}
    monkeypatch.setenv("KANON_REASONING_EFFORT", "medium")
    monkeypatch.setattr(llm, "init_chat_model", lambda model, **kwargs: captured.update(
        model=model, **kwargs
    ))

    llm.chat_model(
        SimpleNamespace(rag_model="unused", judge_model="anthropic:claude-example"),
        role="judge",
    )

    assert captured == {"model": "anthropic:claude-example"}
