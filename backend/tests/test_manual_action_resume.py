"""
The "third option" Commander asked for: when a worker task hits something
only a human can clear (a CAPTCHA, an anti-bot wall), it shouldn't be
either fully-manual or permanently stuck. tasks.escalate_task() now opens
a Commander decision-card checkpoint (reusing the existing PENDING_COMMANDER
chat/SMS mechanism) tagged [MANUAL-ACTION-NEEDED], and
tasks.resume_manual_action_tasks() — run on every boot — re-dispatches the
same task once Commander has answered that checkpoint.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.models.task import EscalationType, Task, TaskStatus
from app.services import tasks as task_svc


def _make_engine():
    import app.models.hunter_ledger  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.income_source  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_candidate(session: Session, cid: str = "HUNTER-CAND-CAPTCHA", **overrides) -> CanonicalOpportunity:
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


def test_escalate_task_with_commander_boundary_opens_pending_commander_checkpoint():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        task = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov"},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )

        task_svc.escalate_task(
            task.task_id,
            EscalationType.commander_boundary,
            "hit a CAPTCHA on the GA unclaimed property portal",
            session,
            page_url="https://gaclaims.unclaimedproperty.com/search",
        )

        opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-CAPTCHA")).first()
        assert opp.disposition == Disposition.pending_commander.value
        assert "[MANUAL-ACTION-NEEDED]" in (opp.required_commander_checkpoints or "")
        assert "CAPTCHA" in opp.required_commander_checkpoints
        assert "gaclaims.unclaimedproperty.com" in opp.required_commander_checkpoints


def test_escalate_task_with_non_boundary_type_does_not_open_checkpoint():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        task = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov"},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )

        task_svc.escalate_task(
            task.task_id,
            EscalationType.unrecoverable_failure,
            "portal returned a 500 error",
            session,
        )

        opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-CAPTCHA")).first()
        assert opp.disposition == Disposition.watchlist.value
        assert opp.required_commander_checkpoints is None


def test_escalate_task_for_non_canonical_opportunity_source_does_not_open_checkpoint():
    engine = _make_engine()
    with Session(engine) as session:
        task = task_svc.dispatch_task(
            task_type="marketplace_listing",
            spec_payload={},
            session=session,
            source_type="income_source",
            source_id="at:some-source",
        )

        task_svc.escalate_task(
            task.task_id,
            EscalationType.commander_boundary,
            "needs a login only Commander has",
            session,
        )
        # No exception, and no canonical opportunity exists to check —
        # the point is _open_manual_action_checkpoint's source_id lookup
        # (via execution_accounting) never fires for a non-ledger source.
        assert task.status == TaskStatus.escalated


def test_resume_dispatches_a_fresh_task_once_commander_has_answered():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        original = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov", "business_name": "Murphy Enterprises LLC"},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
            priority=10,
            max_attempts=2,
        )
        task_svc.escalate_task(
            original.task_id,
            EscalationType.commander_boundary,
            "hit a CAPTCHA",
            session,
        )

        opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-CAPTCHA")).first()
        opp.commander_response = "resolved it myself, try again"
        opp.commander_responded_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        session.add(opp)
        session.commit()

        resumed = task_svc.resume_manual_action_tasks(session)

        assert len(resumed) == 1
        new_task = resumed[0]
        assert new_task.task_id != original.task_id
        assert new_task.task_type == "government_portal_search"
        assert new_task.priority == 10
        assert new_task.max_attempts == 2
        spec = json.loads(new_task.spec_payload)
        assert spec["business_name"] == "Murphy Enterprises LLC"

        all_tasks_for_source = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-CAPTCHA")
        ).all()
        assert len(all_tasks_for_source) == 2


def test_answer_endpoint_immediately_resumes_manual_action_task():
    from app.routers.hunter_ledger import answer_commander_checkpoint

    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        original = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov", "business_name": "Murphy Enterprises LLC"},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )
        task_svc.escalate_task(
            original.task_id, EscalationType.commander_boundary, "hit a CAPTCHA", session,
        )

        answer_commander_checkpoint(
            "HUNTER-CAND-CAPTCHA", "try again", session=session
        )

        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-CAPTCHA")
        ).all()
        assert len(tasks) == 2
        assert any(task.status == TaskStatus.dispatched for task in tasks)


def test_resume_is_idempotent_for_the_same_answer_timestamp():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        original = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov"},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )
        task_svc.escalate_task(
            original.task_id, EscalationType.commander_boundary, "hit a CAPTCHA", session,
        )
        opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-CAPTCHA")).first()
        opp.commander_response = "try again"
        opp.commander_responded_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        session.add(opp)
        session.commit()

        first_run = task_svc.resume_manual_action_tasks(session)
        second_run = task_svc.resume_manual_action_tasks(session)

        assert len(first_run) == 1
        assert len(second_run) == 1
        assert first_run[0].task_id == second_run[0].task_id


def test_resume_skips_candidates_without_the_manual_action_tag():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(
            session,
            disposition=Disposition.pending_commander.value,
            commander_response="answered a research question",
            commander_responded_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
            required_commander_checkpoints="Need your full legal name and email.",
        )

        resumed = task_svc.resume_manual_action_tasks(session)

        assert resumed == []


def test_resume_skips_when_no_escalated_task_exists_for_the_candidate():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(
            session,
            disposition=Disposition.pending_commander.value,
            commander_response="try again",
            commander_responded_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
            required_commander_checkpoints="[MANUAL-ACTION-NEEDED] hit a CAPTCHA",
        )
        # No Task row at all for this source_id.

        resumed = task_svc.resume_manual_action_tasks(session)

        assert resumed == []


def test_resume_skips_candidates_still_unanswered():
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(
            session,
            disposition=Disposition.pending_commander.value,
            commander_response=None,
            required_commander_checkpoints="[MANUAL-ACTION-NEEDED] hit a CAPTCHA",
        )
        task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )

        resumed = task_svc.resume_manual_action_tasks(session)

        assert resumed == []


def test_checkpoint_failure_never_blocks_the_escalation_itself(monkeypatch):
    engine = _make_engine()
    with Session(engine) as session:
        _seed_candidate(session)
        task = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={},
            session=session,
            source_type="canonical_opportunity",
            source_id="HUNTER-CAND-CAPTCHA",
        )

        from app.services import execution_accounting as acct

        def _boom(*a, **k):
            raise RuntimeError("simulated ledger outage")

        monkeypatch.setattr(acct, "set_disposition", _boom)

        result = task_svc.escalate_task(
            task.task_id, EscalationType.commander_boundary, "hit a CAPTCHA", session,
        )

        assert result.status == TaskStatus.escalated
