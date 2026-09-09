"""
Commander had no way to know a checkpoint was waiting unless they opened
the Hunter AI chat widget themselves — set_disposition() now raises a
high-priority Alert whenever a candidate moves to PENDING_COMMANDER with
no answer yet, reusing the existing alert_svc -> Twilio SMS pipeline
already wired for high/critical alerts (see app/services/sms.py).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.alert import Alert
from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services import execution_accounting as acct


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401
    import app.models.alert  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_candidate(session: Session, cid: str = "NOTIFY-CAND", **overrides) -> CanonicalOpportunity:
    fields = dict(
        canonical_opportunity_id=cid,
        lane="compliance_recovery",
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.watchlist.value,
    )
    fields.update(overrides)
    opp = CanonicalOpportunity(**fields)
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_pending_commander_transition_raises_a_high_priority_alert():
    session = _make_session()
    _make_candidate(session)

    acct.set_disposition(
        session, "NOTIFY-CAND", Disposition.pending_commander,
        new_checkpoint="Need your full name and email.",
    )

    alerts = session.exec(select(Alert)).all()
    assert len(alerts) == 1
    assert alerts[0].priority == "high"
    assert "NOTIFY-CAND" in alerts[0].title
    assert "full name and email" in alerts[0].body


def test_alert_triggers_sms_send_for_high_priority():
    session = _make_session()
    _make_candidate(session)

    with patch("app.services.sms.send_alert_sms") as mock_sms:
        acct.set_disposition(
            session, "NOTIFY-CAND", Disposition.pending_commander,
            new_checkpoint="Need your SSN to file this claim.",
        )

    mock_sms.assert_called_once()


def test_no_alert_when_transitioning_to_a_disposition_other_than_pending_commander():
    session = _make_session()
    _make_candidate(session)
    acct.record_rescue_attempt(session, "NOTIFY-CAND", "alternate_channel", "checked", result="found")

    acct.set_disposition(session, "NOTIFY-CAND", Disposition.blocked, evidence="no lawful path found")

    assert session.exec(select(Alert)).all() == []


def test_no_alert_when_commander_has_already_answered():
    """A disposition transition to pending_commander that already carries
    an answer (no new_checkpoint given, so the prior answer isn't
    cleared) must not fire a 'you have something waiting' notification —
    there's nothing new to notify about."""
    session = _make_session()
    _make_candidate(session, commander_response="already answered")

    acct.set_disposition(session, "NOTIFY-CAND", Disposition.pending_commander)

    assert session.exec(select(Alert)).all() == []


def test_notification_failure_never_blocks_the_disposition_change(monkeypatch):
    session = _make_session()
    _make_candidate(session)

    import app.services.alerts as alert_svc

    def _boom(*a, **k):
        raise RuntimeError("simulated alert-service outage")

    monkeypatch.setattr(alert_svc, "raise_alert", _boom)

    opp = acct.set_disposition(
        session, "NOTIFY-CAND", Disposition.pending_commander,
        new_checkpoint="Need your full name.",
    )
    assert opp.disposition == Disposition.pending_commander.value
