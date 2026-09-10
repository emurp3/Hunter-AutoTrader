"""
Recovery-board Track 4 (Commander, 2026-09-10): "drafting is not sending."
_execute_service_outreach now actually sends the drafted email via the
existing SMTP infrastructure (app.services.email_notify) when a real
contact_email is present and SMTP is configured — falling back to
draft-only, exactly as before, when it isn't. email_sent is a real,
confirmed-or-not SMTP outcome, never inferred.
"""

from __future__ import annotations

from app.worker import executors


def _task(task_id: str = "task-1") -> dict:
    return {"task_id": task_id, "task_type": "service_outreach"}


def test_sends_real_email_when_contact_email_and_smtp_configured(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Hello, quick question about your business.")

    from app.services import email_notify
    sent_calls = []
    monkeypatch.setattr(
        email_notify, "send_email_to",
        lambda to, subject, body: (sent_calls.append((to, subject, body)), True)[1],
    )

    spec = {
        "service_outreach": {"contact_email": "owner@localbiz.example", "business_type": "bakery"},
        "category": "service",
    }
    result = executors._execute_service_outreach(_task(), spec)

    assert result.outcome["email_sent"] is True
    assert result.outcome["send_error"] is None
    assert result.outcome["draft_created"] is True
    assert sent_calls == [("owner@localbiz.example", "Regarding your bakery — quick question", "Hello, quick question about your business.")]
    assert "sent real outreach email" in result.notes


def test_falls_back_to_draft_only_when_smtp_not_configured(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")

    from app.services import email_notify
    monkeypatch.setattr(email_notify, "send_email_to", lambda to, subject, body: False)

    spec = {"service_outreach": {"contact_email": "owner@localbiz.example"}}
    result = executors._execute_service_outreach(_task(), spec)

    assert result.outcome["email_sent"] is False
    assert result.outcome["send_error"] == "SMTP not configured or send failed — see worker logs"
    assert result.outcome["draft_created"] is True
    assert "not sent" in result.notes


def test_no_send_attempted_when_only_contact_url_given(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")

    from app.services import email_notify
    calls = []
    monkeypatch.setattr(email_notify, "send_email_to", lambda *a: calls.append(a) or True)

    spec = {"service_outreach": {"contact_url": "https://localbiz.example/contact"}}
    result = executors._execute_service_outreach(_task(), spec)

    assert calls == []
    assert result.outcome["email_sent"] is False
    assert result.outcome["send_error"] is None


def test_send_exception_is_captured_not_raised(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")

    from app.services import email_notify

    def _boom(to, subject, body):
        raise RuntimeError("smtp connection reset")

    monkeypatch.setattr(email_notify, "send_email_to", _boom)

    spec = {"service_outreach": {"contact_email": "owner@localbiz.example"}}
    result = executors._execute_service_outreach(_task(), spec)

    assert result.outcome["email_sent"] is False
    assert "smtp connection reset" in result.outcome["send_error"]


def test_raises_when_no_contact_route_at_all(monkeypatch):
    import pytest
    from app.worker.executors import WorkerExecutionError

    with pytest.raises(WorkerExecutionError):
        executors._execute_service_outreach(_task(), {"service_outreach": {}})
