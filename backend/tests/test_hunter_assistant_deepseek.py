from types import SimpleNamespace

from app.routers import assistant


def _ctx():
    return {"account_cash": "0", "top_opps": [], "signals_total": 0}


def test_chat_uses_deepseek_v41_and_preserves_history(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="I am Hunter."))],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}),
            )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr("openai.OpenAI", Client)
    monkeypatch.setattr(assistant, "_gather_context", lambda _: _ctx())
    monkeypatch.setattr(assistant, "_build_system_prompt", lambda _: "You are Hunter AI.")

    result = assistant.chat(
        assistant.ChatRequest(
            message="What did I ask?",
            history=[
                assistant.ChatMessage(role="user", content="Who are you?"),
                assistant.ChatMessage(role="assistant", content="I am Hunter."),
            ],
        ),
        session=object(),
    )

    assert captured["client"]["base_url"] == "https://api.deepseek.com"
    assert captured["request"]["model"] == "deepseek-flash"
    assert captured["request"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["request"]["messages"][1:3] == [
        {"role": "user", "content": "Who are you?"},
        {"role": "assistant", "content": "I am Hunter."},
    ]
    assert result.provider == "deepseek"
    assert result.error_code is None
    assert result.usage["total_tokens"] == 14


def test_provider_errors_are_specific():
    class ProviderError(Exception):
        def __init__(self, status_code, message):
            self.status_code = status_code
            super().__init__(message)

    cases = [
        (ProviderError(401, "bad key"), "provider_authentication"),
        (ProviderError(429, "rate limit"), "provider_rate_limit"),
        (ProviderError(500, "down"), "provider_unavailable"),
        (TimeoutError("request timeout"), "provider_timeout"),
        (RuntimeError("insufficient balance"), "provider_balance"),
    ]
    for exc, expected in cases:
        assert assistant._provider_error(exc)[0] == expected
