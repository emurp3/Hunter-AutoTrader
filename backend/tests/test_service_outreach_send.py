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


def test_contact_url_only_triggers_a_real_website_investigation_and_sends_if_found(monkeypatch):
    """Commander, 2026-09-11: a contact_url alone is not a dead end —
    Hunter investigates the published website (via the existing
    Playwright infrastructure) before giving up. When that investigation
    finds a real, published email, it's used and the verification
    evidence (source URL, timestamp) travels with the outcome."""
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")

    verification = {
        "contact_email": "owner@localbiz.example",
        "verification_source_url": "https://localbiz.example/contact",
        "verified_at": "2026-09-11T00:00:00+00:00",
    }
    monkeypatch.setattr(executors, "_research_contact_email_from_website", lambda url: verification)

    from app.services import email_notify
    calls = []
    monkeypatch.setattr(
        email_notify, "send_email_to",
        lambda to, subject, body: (calls.append((to, subject, body)), True)[1],
    )

    spec = {"service_outreach": {"contact_url": "https://localbiz.example/contact"}}
    result = executors._execute_service_outreach(_task(), spec)

    assert calls and calls[0][0] == "owner@localbiz.example"
    assert result.outcome["email_sent"] is True
    assert result.outcome["contact_verification"] == verification


def test_contact_url_only_escalates_truthfully_when_investigation_finds_nothing(monkeypatch):
    """No email published anywhere the crawl looked — a real, researched
    "no route" outcome, not a guess and not a silent draft-only skip."""
    import pytest
    from app.worker.executors import WorkerExecutionError

    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")
    monkeypatch.setattr(executors, "_research_contact_email_from_website", lambda url: None)

    from app.services import email_notify
    calls = []
    monkeypatch.setattr(email_notify, "send_email_to", lambda *a: calls.append(a) or True)

    spec = {"service_outreach": {"contact_url": "https://localbiz.example/contact"}}
    with pytest.raises(WorkerExecutionError) as excinfo:
        executors._execute_service_outreach(_task(), spec)

    assert excinfo.value.escalation_type == "contact_unavailable"
    assert calls == []  # never sent anything


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


def test_no_website_at_all_proposes_and_verifies_a_candidate_then_sends(monkeypatch):
    """Commander, 2026-09-11: "do not confuse a missing source field
    with an impossible opportunity." When the discovery source gave no
    website at all, Hunter proposes a candidate official site (via its
    own existing Claude integration) and independently verifies it
    before ever treating it as a real contact route."""
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")
    monkeypatch.setattr(
        executors, "_propose_candidate_business_website",
        lambda name, btype, loc: "https://example-dental.example",
    )

    verification = {
        "contact_email": "front-desk@example-dental.example",
        "verification_source_url": "https://example-dental.example",
        "verified_at": "2026-09-11T00:00:00+00:00",
        "website_candidate": True,
    }

    def _fake_research(url, *, expected_business_name=None):
        assert url == "https://example-dental.example"
        assert expected_business_name == "Example Dental"
        return verification

    monkeypatch.setattr(executors, "_research_contact_email_from_website", _fake_research)

    from app.services import email_notify
    calls = []
    monkeypatch.setattr(
        email_notify, "send_email_to",
        lambda to, subject, body: (calls.append((to, subject, body)), True)[1],
    )

    spec = {"service_outreach": {"business_name": "Example Dental", "business_type": "Example Dental"}}
    result = executors._execute_service_outreach(_task(), spec)

    assert calls and calls[0][0] == "front-desk@example-dental.example"
    assert result.outcome["email_sent"] is True
    assert result.outcome["contact_verification"]["website_candidate"] is True


def test_no_website_and_no_verified_candidate_escalates_truthfully(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "Draft body")
    monkeypatch.setattr(
        executors, "_propose_candidate_business_website",
        lambda name, btype, loc: "https://wrong-site.example",
    )
    monkeypatch.setattr(executors, "_research_contact_email_from_website", lambda url, **kw: None)

    from app.services import email_notify
    calls = []
    monkeypatch.setattr(email_notify, "send_email_to", lambda *a: calls.append(a) or True)

    import pytest
    from app.worker.executors import WorkerExecutionError

    spec = {"service_outreach": {"business_name": "Example Dental"}}
    with pytest.raises(WorkerExecutionError) as excinfo:
        executors._execute_service_outreach(_task(), spec)

    assert excinfo.value.escalation_type == "contact_unavailable"
    assert calls == []


def test_no_website_and_no_business_name_never_calls_claude_for_a_candidate(monkeypatch):
    """No name to research at all (e.g. only a generic category) — must
    not attempt a guess-prone candidate lookup; straight to the honest
    no-route escalation."""
    calls = []
    monkeypatch.setattr(
        executors, "_propose_candidate_business_website",
        lambda *a: calls.append(a) or None,
    )

    import pytest
    from app.worker.executors import WorkerExecutionError

    spec = {"service_outreach": {"business_type": "healthcare-implementation"}}
    with pytest.raises(WorkerExecutionError):
        executors._execute_service_outreach(_task(), spec)

    assert calls == []


def test_raises_when_no_contact_route_at_all(monkeypatch):
    import pytest
    from app.worker.executors import WorkerExecutionError

    with pytest.raises(WorkerExecutionError):
        executors._execute_service_outreach(_task(), {"service_outreach": {}})
