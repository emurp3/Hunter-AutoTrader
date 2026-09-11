"""SMTP outreach with a mandatory durable send permit, not exactly-once SMTP.

No automatic resend after a permit is consumed, even if the acknowledgement
or worker is lost. Connection/authentication errors happen before the permit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, make_msgid, parseaddr
import smtplib
import ssl
from typing import Callable

from app.services import email_notify as config


class PreSendError(Exception):
    def __init__(self, reason: str, *, configuration: bool = False):
        super().__init__(reason)
        self.configuration = configuration


class PermitError(Exception):
    pass


def _address(value: str) -> str:
    value = (value or "").strip()
    if not value.isascii():
        raise PreSendError("SMTPUTF8 addresses are not supported by this transport")
    if any(c in value for c in "\r\n,;") or parseaddr(value)[1] != value or value.count("@") != 1:
        raise PreSendError("Expected exactly one bare email address")
    local, domain = value.split("@")
    if not local or "." not in domain or any(c.isspace() for c in value):
        raise PreSendError("Invalid email address")
    return value


def send_outreach(to: str, subject: str, body: str, *, before_send: Callable,
                  contact_verification: dict | None = None) -> dict:
    missing = [name for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD") if not getattr(config, name)]
    if missing:
        raise PreSendError("Missing worker settings: " + ", ".join(missing), configuration=True)
    if config.SMTP_PORT not in (465, 587):
        raise PreSendError("SMTP_PORT must be 465 (implicit TLS) or 587 (STARTTLS)", configuration=True)
    sender, recipient = _address(config.SMTP_USERNAME), _address(to)
    if not body.strip() or any(c in subject + config.SMTP_FROM_NAME for c in "\r\n"):
        raise PreSendError("Empty body or invalid message headers")
    if not callable(before_send):
        raise PreSendError("Durable outreach send guard unavailable")
    message = EmailMessage()
    message["From"] = formataddr((config.SMTP_FROM_NAME, sender))
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    wire = message.as_bytes(policy=SMTP_POLICY)
    intent = {
        "from": sender, "to": recipient, "subject": subject, "body": body,
        "message_id": message["Message-ID"], "smtp_host": config.SMTP_HOST,
        "smtp_port": config.SMTP_PORT, "contact_verification": contact_verification,
    }
    server = None
    try:
        context = ssl.create_default_context()
        try:
            if config.SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(config.SMTP_HOST, 465, timeout=20, context=context)
            else:
                server = smtplib.SMTP(config.SMTP_HOST, 587, timeout=20)
                server.ehlo()
                server.starttls(context=context)
            server.ehlo()
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        except Exception as exc:
            # Do not include exception text: providers may echo account data.
            raise PreSendError("SMTP connection/TLS/authentication failed before send: " + type(exc).__name__,
                               configuration=True) from exc
        try:
            before_send(intent)
        except Exception as exc:
            raise PermitError("Durable send permit not acknowledged; no SMTP submission attempted") from exc
        receipt = {"message_id": message["Message-ID"], "smtp_host": config.SMTP_HOST,
                   "recipient": recipient, "recorded_at": datetime.now(timezone.utc).isoformat()}
        # SMTP.sendmail does not expose the final acceptance reply. Use the
        # supported envelope/DATA methods so the actual SMTP receipt survives.
        phase = "mail"
        try:
            code, reply = server.mail(sender)
            if code != 250:
                return {**receipt, "send_status": "rejected", "phase": phase, "smtp_code": code,
                        "smtp_reply": reply.decode(errors="replace"), "email_sent": False}
            phase = "rcpt"
            code, reply = server.rcpt(recipient)
            if code not in (250, 251):
                return {**receipt, "send_status": "rejected", "phase": phase, "smtp_code": code,
                        "smtp_reply": reply.decode(errors="replace"), "email_sent": False}
            phase = "data"
            code, reply = server.data(wire)
            return {**receipt, "send_status": "accepted" if code == 250 else "rejected",
                    "phase": phase, "smtp_code": code, "smtp_reply": reply.decode(errors="replace"),
                    "email_sent": code == 250}
        except smtplib.SMTPResponseException as exc:
            return {**receipt, "send_status": "rejected", "phase": phase,
                    "smtp_code": exc.smtp_code, "smtp_reply": exc.smtp_error.decode(errors="replace"),
                    "email_sent": False}
        except Exception as exc:
            return {**receipt, "send_status": "uncertain", "phase": phase,
                    "error_type": type(exc).__name__, "email_sent": False}
    finally:
        if server is not None:
            # A QUIT failure must never turn an accepted DATA reply into failure.
            try:
                server.close()
            except Exception:
                pass
