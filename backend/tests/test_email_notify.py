"""
send_email_to() extracted from the existing Commander-only SMTP plumbing
(email_notify.send_email/send_alert_email) so it can send to an arbitrary
real recipient — used by the service-outreach executor to actually send
a drafted cold-outreach email instead of only drafting it.
"""

from __future__ import annotations

from app.services import email_notify


def test_send_email_to_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.setattr(email_notify, "SMTP_HOST", "")
    monkeypatch.setattr(email_notify, "SMTP_USERNAME", "")
    monkeypatch.setattr(email_notify, "SMTP_PASSWORD", "")

    result = email_notify.send_email_to("prospect@example.com", "Hi", "body")

    assert result is False


def test_send_email_to_rejects_empty_recipient(monkeypatch):
    monkeypatch.setattr(email_notify, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_notify, "SMTP_USERNAME", "hunter@example.com")
    monkeypatch.setattr(email_notify, "SMTP_PASSWORD", "app-password")

    assert email_notify.send_email_to("", "Hi", "body") is False
    assert email_notify.send_email_to("   ", "Hi", "body") is False


def test_send_email_to_sends_via_smtp_to_the_real_recipient(monkeypatch):
    monkeypatch.setattr(email_notify, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_notify, "SMTP_USERNAME", "hunter@example.com")
    monkeypatch.setattr(email_notify, "SMTP_PASSWORD", "app-password")

    sent = {}

    class _FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, pw):
            sent["login"] = (user, pw)

        def sendmail(self, from_addr, to_addr, message):
            sent["from"] = from_addr
            sent["to"] = to_addr
            sent["message"] = message

    monkeypatch.setattr(email_notify.smtplib, "SMTP", lambda host, port, timeout=15: _FakeServer())

    result = email_notify.send_email_to("prospect@example.com", "Regarding your business", "Hello there")

    assert result is True
    assert sent["to"] == "prospect@example.com"
    assert "To: prospect@example.com" in sent["message"]
    assert "Regarding your business" in sent["message"]
    assert "Hello there" in sent["message"]


def test_send_email_to_does_not_require_commander_email_configured(monkeypatch):
    """send_email_to should work even if COMMANDER_EMAIL was never set —
    it's sending to a real prospect, not to Commander."""
    monkeypatch.setattr(email_notify, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_notify, "SMTP_USERNAME", "hunter@example.com")
    monkeypatch.setattr(email_notify, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(email_notify, "COMMANDER_EMAIL", "")

    class _FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, pw):
            pass

        def sendmail(self, from_addr, to_addr, message):
            pass

    monkeypatch.setattr(email_notify.smtplib, "SMTP", lambda host, port, timeout=15: _FakeServer())

    assert email_notify.send_email_to("prospect@example.com", "Hi", "body") is True
