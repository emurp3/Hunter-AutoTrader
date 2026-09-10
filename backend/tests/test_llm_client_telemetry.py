"""
Commander's bounded observability correction (2026-09-10): call_llm_json()
must report which provider/model actually produced a reasoning response,
and whether a fallback past the primary (grok) occurred — telemetry only,
no change to provider order, control flow, or capability restrictions.

All HTTP is served by an in-process httpx.MockTransport — no live network,
no live model calls, no real API keys.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.research.generic.llm_client import call_llm_json


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_telemetry_reports_primary_provider_with_no_fallback(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.x.ai" in str(request.url)
        return _chat_response('{"action": "give_up"}')

    telemetry: dict = {}
    with _client(handler) as client:
        result = call_llm_json("sys", "user", client=client, telemetry=telemetry)

    assert result == {"action": "give_up"}
    assert telemetry["provider"] == "grok"
    assert telemetry["model"] == "grok-3"
    assert telemetry["fallback_occurred"] is False
    assert telemetry["fallback_reason"] is None


def test_telemetry_reports_fallback_when_primary_fails(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("VENICE_API_KEY", "test-venice-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.x.ai" in str(request.url):
            return httpx.Response(500, json={"error": "internal"})
        assert "api.venice.ai" in str(request.url)
        return _chat_response('{"action": "click"}')

    telemetry: dict = {}
    with _client(handler) as client:
        result = call_llm_json("sys", "user", client=client, telemetry=telemetry)

    assert result == {"action": "click"}
    assert telemetry["provider"] == "venice"
    assert telemetry["model"] == "llama-3.3-70b"
    assert telemetry["fallback_occurred"] is True
    assert "grok" in telemetry["fallback_reason"]
    assert "test-grok-key" not in telemetry["fallback_reason"]


def test_telemetry_reports_fallback_when_primary_not_configured(monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "test-venice-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response('{"action": "click"}')

    telemetry: dict = {}
    with _client(handler) as client:
        call_llm_json("sys", "user", client=client, telemetry=telemetry)

    assert telemetry["provider"] == "venice"
    assert telemetry["fallback_occurred"] is True
    assert "grok" in telemetry["fallback_reason"]
    assert "not_configured" in telemetry["fallback_reason"]


def test_telemetry_when_no_advisor_configured_at_all(monkeypatch):
    for env_var in ("GROK_API_KEY", "VENICE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call should be made when nothing is configured")

    telemetry: dict = {}
    with _client(handler) as client:
        result = call_llm_json("sys", "user", client=client, telemetry=telemetry)

    assert result is None
    assert telemetry["provider"] is None
    assert telemetry["model"] is None
    assert telemetry["fallback_occurred"] is None
    # Each provider's own not-configured state is reported — more useful
    # than a single generic string.
    assert "grok: not_configured" in telemetry["fallback_reason"]
    assert "venice: not_configured" in telemetry["fallback_reason"]
    assert "deepseek: not_configured" in telemetry["fallback_reason"]


def test_telemetry_when_every_provider_fails(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.setenv("VENICE_API_KEY", "test-venice-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    telemetry: dict = {}
    with _client(handler) as client:
        result = call_llm_json("sys", "user", client=client, telemetry=telemetry)

    assert result is None
    assert telemetry["provider"] is None
    assert "grok" in telemetry["fallback_reason"]
    assert "venice" in telemetry["fallback_reason"]
    assert "deepseek" in telemetry["fallback_reason"]
    assert "test-grok-key" not in telemetry["fallback_reason"]
    assert "test-venice-key" not in telemetry["fallback_reason"]
    assert "test-deepseek-key" not in telemetry["fallback_reason"]


def test_call_without_telemetry_argument_is_unaffected(monkeypatch):
    """Backward compatibility — every existing caller (the research
    engine) that doesn't pass telemetry must behave exactly as before."""
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response('{"action": "give_up"}')

    with _client(handler) as client:
        result = call_llm_json("sys", "user", client=client)

    assert result == {"action": "give_up"}


def test_telemetry_never_contains_prompt_content(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-grok-key")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response('{"action": "give_up"}')

    telemetry: dict = {}
    with _client(handler) as client:
        call_llm_json(
            "system prompt with secret instructions",
            "user prompt mentioning Commander's SSN 123-45-6789",
            client=client, telemetry=telemetry,
        )

    serialized = str(telemetry)
    assert "123-45-6789" not in serialized
    assert "secret instructions" not in serialized
