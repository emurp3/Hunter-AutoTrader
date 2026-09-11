"""
Demonstrated failure (2026-09-10, EOD recovery): a real 502 from Render's
load balancer during the web service's own redeploy hit the /escalate
callback for a task whose outcome was already decided (no contact route
— nothing external had happened yet). With no retry, the task sat in
`executing` with no way to close out until its 120s lease expired, at
which point stale-lease reclaim replayed the whole task from scratch.
For a send-capable task this same gap could replay a real SMTP send.

These tests verify HunterWorkerClient's terminal callbacks (complete/
fail/escalate) retry a transient 5xx/transport error a bounded number
of times, succeed once the server recovers, never retry a real 4xx
rejection, and give up (raising) once retries are exhausted.
"""

from __future__ import annotations

import httpx
import pytest

from app.worker.client import HunterWorkerClient


def _client(monkeypatch, transport: httpx.MockTransport) -> HunterWorkerClient:
    monkeypatch.setenv("HUNTER_BASE_URL", "https://hunter.example")
    monkeypatch.setattr("app.worker.client.time.sleep", lambda _seconds: None)
    client = HunterWorkerClient()
    client._client = httpx.Client(base_url="https://hunter.example", transport=transport)
    return client


def test_escalate_retries_past_a_transient_502_and_succeeds(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"task_id": "t1", "status": "escalated"})

    client = _client(monkeypatch, httpx.MockTransport(handler))

    result = client.escalate(
        "t1", "worker-1", escalation_type="contact_unavailable", reason="No contact route available"
    )

    assert len(calls) == 3
    assert result["status"] == "escalated"


def test_complete_never_retries_a_real_4xx_rejection(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"detail": "Task not found"})

    client = _client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        client.complete("missing-task", "worker-1", outcome={})

    assert len(calls) == 1  # no retry burned on a genuine rejection


def test_fail_gives_up_after_exhausting_retries_on_persistent_5xx(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, text="Service Unavailable")

    client = _client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        client.fail("t1", "worker-1", reason="boom")

    assert len(calls) == 4  # initial attempt + 3 retries, then give up


def test_record_pending_outcome_retries_past_a_transient_502_and_succeeds(monkeypatch):
    """Commander, 2026-09-11: the durable pending-outcome record must be
    just as resilient to a brief redeploy-window 502 as complete/fail/
    escalate — it's reporting the same kind of already-decided outcome."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 2:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"task_id": "t1", "status": "executing"})

    client = _client(monkeypatch, httpx.MockTransport(handler))

    result = client.record_pending_outcome("t1", "worker-1", outcome={"email_sent": True})

    assert len(calls) == 2
    assert result["task_id"] == "t1"


def test_escalate_retries_past_a_transport_level_error(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 2:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json={"task_id": "t1", "status": "escalated"})

    client = _client(monkeypatch, httpx.MockTransport(handler))

    result = client.escalate("t1", "worker-1", escalation_type="unrecoverable_failure", reason="x")

    assert len(calls) == 2
    assert result["status"] == "escalated"
