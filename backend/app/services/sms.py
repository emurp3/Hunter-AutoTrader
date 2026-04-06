"""
SMS notification service — Twilio outbound SMS to Commander.

Sends SMS for HIGH and CRITICAL alerts only. Medium/low alerts are
surfaced in the Hunter alerts UI but do not generate SMS noise.

Required env vars (set in Render dashboard):
    TWILIO_ACCOUNT_SID    — from console.twilio.com
    TWILIO_AUTH_TOKEN     — from console.twilio.com
    TWILIO_FROM_NUMBER    — your Twilio phone number (+1XXXXXXXXXX)
    COMMANDER_PHONE       — destination number (+14782319790)

SMS is non-blocking. If Twilio is not configured or the send fails,
a warning is logged and the alert is still persisted normally.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "+13502250005")
COMMANDER_PHONE: str = os.getenv("COMMANDER_PHONE", "+14782319790")

# Alert priorities that trigger SMS
_SMS_PRIORITIES = {"high", "critical"}

# Max SMS body length (Twilio splits at 160; we keep it tight)
_MAX_SMS_CHARS = 155


def send_alert_sms(title: str, body: str, priority: str) -> bool:
    """
    Send an SMS to Commander for high/critical alerts.

    Returns True if sent, False if skipped or failed.
    Never raises — all failures are logged as warnings.
    """
    if priority not in _SMS_PRIORITIES:
        return False

    if not _is_configured():
        logger.debug("SMS not configured — skipping notification for: %s", title)
        return False

    prefix = "URGENT — " if priority == "critical" else ""
    message = _format_sms(prefix + title, body)

    return _send(message)


def send_sms(message: str) -> bool:
    """
    Send a raw SMS message to Commander.
    Use for direct one-off notifications not tied to an alert.
    """
    if not _is_configured():
        logger.warning("SMS not configured (TWILIO_* env vars missing)")
        return False
    return _send(message[:_MAX_SMS_CHARS])


def _is_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER and COMMANDER_PHONE)


def _format_sms(title: str, body: str) -> str:
    """Compose a tight SMS that fits in 155 chars."""
    full = f"Hunter: {title}"
    if len(full) < _MAX_SMS_CHARS - 10:
        # Append a short snippet of the body if it fits
        snippet = body.replace("\n", " ")[:60].strip()
        candidate = f"{full} | {snippet}"
        if len(candidate) <= _MAX_SMS_CHARS:
            return candidate
    return full[:_MAX_SMS_CHARS]


def _send(message: str) -> bool:
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=COMMANDER_PHONE,
        )
        logger.info("SMS sent to %s — SID=%s", COMMANDER_PHONE, msg.sid)
        return True
    except ImportError:
        logger.warning("twilio package not installed — run: pip install twilio")
        return False
    except Exception as exc:
        logger.warning("SMS send failed: %s", exc)
        return False
