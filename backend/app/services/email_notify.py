"""
Email notification service — sends Commander alerts via SMTP.

Fires on HIGH and CRITICAL alerts (same threshold as SMS).
Uses standard smtplib — no extra packages required.

Required env vars (set in Render dashboard):
    SMTP_HOST        — e.g. smtp.gmail.com / smtp.aol.com / smtp.office365.com
    SMTP_PORT        — 587 (TLS) or 465 (SSL). Default: 587
    SMTP_USERNAME    — the sending email address
    SMTP_PASSWORD    — app password (NOT your regular login password)
    SMTP_FROM_NAME   — display name, default "Hunter"
    COMMANDER_EMAIL  — destination address, default beautillion1@aol.com

App password setup:
    Gmail:   myaccount.google.com → Security → 2-Step → App passwords
    AOL:     login.aol.com → Account Security → Generate app password
    Outlook: account.microsoft.com → Security → Advanced → App passwords
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "Hunter")
COMMANDER_EMAIL: str = os.getenv("COMMANDER_EMAIL", "beautillion1@aol.com")

_EMAIL_PRIORITIES = {"high", "critical"}


def send_alert_email(title: str, body: str, priority: str) -> bool:
    """
    Send an email to Commander for high/critical alerts.
    Returns True if sent, False if skipped or failed. Never raises.
    """
    if priority not in _EMAIL_PRIORITIES:
        return False
    if not _is_configured():
        logger.debug("Email not configured — skipping: %s", title)
        return False

    subject = f"[Hunter {'URGENT' if priority == 'critical' else 'Alert'}] {title}"
    return _send(subject, body)


def send_email(subject: str, body: str) -> bool:
    """Send a direct email to Commander. Use for one-off notifications."""
    if not _is_configured():
        logger.warning("Email not configured (SMTP_* env vars missing)")
        return False
    return _send(subject, body)


def _is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and COMMANDER_EMAIL)


def _send(subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USERNAME}>"
        msg["To"] = COMMANDER_EMAIL

        # Plain text part
        msg.attach(MIMEText(body, "plain"))

        # HTML part — simple but readable
        html = f"""<html><body>
<p style="font-family:monospace;font-size:14px;color:#111;">
<strong>{subject}</strong><br><br>
{body.replace(chr(10), '<br>')}
</p>
<p style="font-size:11px;color:#888;margin-top:24px;">
Hunter v0.2.0 &mdash; autonomous revenue engine
</p>
</body></html>"""
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, COMMANDER_EMAIL, msg.as_string())

        logger.info("Email sent to %s — %s", COMMANDER_EMAIL, subject)
        return True

    except Exception as exc:
        logger.warning("Email send failed: %s", exc)
        return False
