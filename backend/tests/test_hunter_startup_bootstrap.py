"""
Tests for the startup bootstrap that carries tonight's two Commander-approved
checkpoints through to the executor from Hunter's own runtime (main.py's
_bootstrap_hunter_ledger_actions_after_startup) — not a manual API call from
Claude or Commander. This is glue over already-tested primitives
(task_svc.dispatch_task, acct.set_disposition); these tests check the glue
itself: which candidates get acted on, and that it's safe to run repeatedly.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import app.database.config as db_config
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.models.task import Task


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


def _seed(
    engine,
    *,
    ucp_commander_response="Murphy Enterprises LLC",
    google_disposition=Disposition.watchlist.value,
    google_already_reopened=False,
    google_commander_response=None,
):
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-01-UCP",
                lane="compliance_recovery",
                factual_mechanism="GA unclaimed property",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.watchlist.value,
                commander_response=ucp_commander_response,
            )
        )
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=google_disposition,
                commander_response=google_commander_response,
                required_commander_checkpoints=(
                    "Need your full legal name, an email or phone, and dates of use."
                    if google_already_reopened else None
                ),
            )
        )
        session.commit()


def _run_bootstrap(engine, monkeypatch):
    monkeypatch.setattr(db_config, "engine", engine)
    from app.main import _bootstrap_hunter_ledger_actions_after_startup

    _bootstrap_hunter_ledger_actions_after_startup()


def test_ucp_dispatch_creates_a_search_task_with_commander_supplied_name(monkeypatch):
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")
        ).all()
        assert len(tasks) == 1
        assert tasks[0].task_type == "government_portal_search"
        spec = tasks[0].spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        assert spec["business_name"] == "Murphy Enterprises LLC"
        assert "gaclaims.unclaimedproperty.com" in spec["search_url"]


def test_ucp_dispatch_is_idempotent_across_repeated_runs(monkeypatch):
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")
        ).all()
        assert len(tasks) == 1


def test_ucp_not_redispatched_once_executed(monkeypatch):
    engine = _make_engine()
    _seed(engine)
    with Session(engine) as session:
        opp = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-01-UCP"
            )
        ).first()
        opp.disposition = Disposition.executed.value
        session.add(opp)
        session.commit()

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")
        ).all()
        assert len(tasks) == 0


def test_ucp_not_dispatched_without_a_commander_supplied_name(monkeypatch):
    engine = _make_engine()
    _seed(engine, ucp_commander_response=None)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")
        ).all()
        assert len(tasks) == 0


def test_google_checkpoint_reopened_with_specific_personal_data_ask(monkeypatch):
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        google = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-02-GOOGLE"
            )
        ).first()
        assert google.disposition == Disposition.pending_commander.value
        assert "full legal name" in google.required_commander_checkpoints
        assert "email or phone" in google.required_commander_checkpoints
        assert google.commander_response is None


def test_google_reopen_does_not_repeat_once_already_reopened(monkeypatch):
    """Regression test for a real bug found live: answering a checkpoint
    moves disposition to WATCHLIST (via record_commander_answer), not
    back to PENDING_COMMANDER — so the reopen check must key off the
    checkpoint TEXT already containing the specific ask, not off
    disposition, or a later run re-reopens and wipes out a real answer
    Commander just gave."""
    engine = _make_engine()
    _seed(
        engine,
        google_disposition=Disposition.watchlist.value,
        google_already_reopened=True,
        google_commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
    )
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        google = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-02-GOOGLE"
            )
        ).first()
        # Untouched — no redundant reopen wiping out the real answer.
        assert not (google.next_action or "").count("[disposition=pending_commander]") > 0
        assert google.commander_response == "Eddie Murphy Jr., eddie@example.com, 2019-2023"


def test_bootstrap_is_a_safe_noop_when_neither_candidate_exists(monkeypatch):
    engine = _make_engine()
    monkeypatch.setattr(db_config, "engine", engine)
    from app.main import _bootstrap_hunter_ledger_actions_after_startup

    _bootstrap_hunter_ledger_actions_after_startup()  # must not raise

    with Session(engine) as session:
        assert session.exec(select(Task)).all() == []


# ── Autonomous submission once Commander's identity fields are on file ─────
# Correction (2026-09-09): the standing rule is narrow — Hunter never
# supplies/invents identity data on Commander's behalf. It is NOT "wait
# for a new approval workflow before submitting anything." Once Commander
# has answered a checkpoint AND the structured identity fields exist,
# Hunter dispatches the real submission itself, no second gate.


def _clear_identity_env(monkeypatch):
    from app.services.commander_identity import FIELD_ENV_VARS
    for env_var in FIELD_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)


def test_ucp_dispatch_includes_no_identity_fields_when_none_on_file(monkeypatch):
    _clear_identity_env(monkeypatch)
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")).first()
        spec = task.spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        assert spec["identity_fields"] == {}


def test_ucp_dispatch_includes_identity_fields_once_on_file(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_CITY", "Macon")
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")).first()
        spec = task.spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        assert spec["identity_fields"]["full_name"] == "Eddie Murphy Jr."
        assert spec["identity_fields"]["city"] == "Macon"
        assert "ssn" not in spec["identity_fields"]  # never included unless actually set


def test_google_no_intake_dispatch_while_awaiting_answer(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    _seed(
        engine,
        google_disposition=Disposition.pending_commander.value,
        google_already_reopened=True,
    )  # reopened, not yet answered
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).all()
        assert tasks == []  # no answer yet — nothing to submit


def test_google_dispatches_intake_submission_once_answered_and_identity_on_file(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
                required_commander_checkpoints="Need your full legal name, email, and dates.",
            )
        )
        session.commit()

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).first()
        assert task is not None
        assert task.task_type == "intake_form_submission"
        spec = task.spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        assert spec["identity_fields"]["full_name"] == "Eddie Murphy Jr."
        assert spec["identity_fields"]["email"] == "eddie@example.com"
        assert "potterhandy.com" in spec["intake_url"]


def test_google_no_intake_dispatch_when_answered_but_no_structured_identity_yet(monkeypatch):
    _clear_identity_env(monkeypatch)  # answered in chat, but no env vars set yet
    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
                required_commander_checkpoints="Need your full legal name, email, and dates.",
            )
        )
        session.commit()

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).all()
        assert tasks == []


def test_ucp_dispatch_never_autonomously_includes_ssn(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_SSN", "123-45-6789")
    engine = _make_engine()
    _seed(engine)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-01-UCP")).first()
        spec = task.spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        # SSN is set and available, but the autonomous dispatch path must
        # never request it — Commander's explicit per-submission consent
        # is required for SSN, so it's not part of this dispatch at all.
        assert "ssn" not in spec["identity_fields"]


def test_google_intake_dispatch_never_autonomously_includes_ssn(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    monkeypatch.setenv("HUNTER_COMMANDER_SSN", "123-45-6789")
    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
                required_commander_checkpoints="Need your full legal name, email, and dates.",
            )
        )
        session.commit()

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).first()
        spec = task.spec_payload
        spec = json.loads(spec) if isinstance(spec, str) else spec
        assert "ssn" not in spec["identity_fields"]


def test_google_intake_dispatch_is_idempotent(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
                required_commander_checkpoints="Need your full legal name, email, and dates.",
            )
        )
        session.commit()

    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).all()
        assert len(tasks) == 1


# ── Stop daily re-execution against a confirmed-dead intake URL ────────────
# Commander, 2026-09-11: "stop daily execution against the known
# GOOGLE-02 404. Route it to investigation of a verified current
# alternative, or a truthful unavailable/waiting disposition." Real
# production evidence: task 6ed846bd reached the intake URL on file and
# found it dead (confirmed via a real Playwright/reasoning run, not
# assumed) — the OLD code would dispatch a fresh attempt against the
# SAME URL again the very next boot (a new date-scoped idempotency key
# every day). These tests cover the fix: the most recent terminal
# attempt against the CURRENT intake_url blocks a same-URL redispatch
# and truthfully reopens the checkpoint instead.


def _seed_google_with_prior_intake_attempt(engine, *, status, intake_url):
    from app.models.task import Task as _Task
    from app.models.task import TaskStatus as _TaskStatus

    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                lane="legal_recovery",
                factual_mechanism="Google Incognito lawsuit intake",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="Eddie Murphy Jr., eddie@example.com, 2019-2023",
                required_commander_checkpoints="Need your full legal name, email, and dates.",
            )
        )
        session.add(
            _Task(
                task_id="prior-intake-task",
                task_type="intake_form_submission",
                source_type="canonical_opportunity",
                source_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
                spec_payload=json.dumps({"intake_url": intake_url}),
                status=_TaskStatus(status),
                failed_at=datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc) if status == "failed" else None,
                escalated_at=datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc) if status == "escalated" else None,
            )
        )
        session.commit()


def test_google_no_redispatch_when_same_intake_url_already_failed(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    _seed_google_with_prior_intake_attempt(
        engine,
        status="failed",
        intake_url="https://potterhandy.com/google-privacy-violations-lawsuit/",
    )

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE",
                Task.task_type == "intake_form_submission",
                Task.task_id != "prior-intake-task",
            )
        ).all()
        assert tasks == []  # no fresh daily attempt against the same dead URL

        google = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-02-GOOGLE"
            )
        ).first()
        assert google.disposition == Disposition.pending_commander.value
        assert "confirmed unreachable" in google.required_commander_checkpoints
        assert google.commander_response is None  # reopened — awaiting a real reply


def test_google_redispatches_when_intake_url_has_changed(monkeypatch):
    """A verified current alternative URL (e.g. an engineering fix to
    INTAKE_URL) must be allowed straight through — this is not a general
    ban on the task type, only on repeating the identical dead URL."""
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    _seed_google_with_prior_intake_attempt(
        engine,
        status="failed",
        intake_url="https://old-dead-url.example/intake",
    )

    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE",
                Task.task_type == "intake_form_submission",
                Task.task_id != "prior-intake-task",
            )
        ).all()
        assert len(tasks) == 1
        spec = json.loads(tasks[0].spec_payload)
        assert "potterhandy.com" in spec["intake_url"]


def test_google_dead_url_reopen_does_not_repeat_across_boots(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.setenv("HUNTER_COMMANDER_FULL_NAME", "Eddie Murphy Jr.")
    monkeypatch.setenv("HUNTER_COMMANDER_EMAIL", "eddie@example.com")
    engine = _make_engine()
    _seed_google_with_prior_intake_attempt(
        engine,
        status="escalated",
        intake_url="https://potterhandy.com/google-privacy-violations-lawsuit/",
    )

    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE",
                Task.task_type == "intake_form_submission",
                Task.task_id != "prior-intake-task",
            )
        ).all()
        assert tasks == []  # never dispatched, on any of the three boots


def test_resume_manual_action_bootstrap_redispatches_after_commander_answer(monkeypatch):
    """The 'third option' bootstrap: a candidate escalated with a
    [MANUAL-ACTION-NEEDED] checkpoint, once Commander answers it, gets
    its escalated task re-dispatched on the next boot — from Hunter's own
    runtime, exactly like the other bootstrap steps."""
    from datetime import datetime, timezone
    from app.models.task import EscalationType, TaskStatus

    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-09-CAPTCHA",
                lane="compliance_recovery",
                factual_mechanism="GA unclaimed property",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response="resolved it myself, try again",
                commander_responded_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
                required_commander_checkpoints="[MANUAL-ACTION-NEEDED] hit a CAPTCHA",
            )
        )
        session.add(
            Task(
                task_id="original-captcha-task",
                task_type="government_portal_search",
                source_type="canonical_opportunity",
                source_id="HUNTER-CAND-2026-09-09-CAPTCHA",
                spec_payload=json.dumps({"search_url": "https://example.gov"}),
                status=TaskStatus.escalated,
                escalation_type=EscalationType.commander_boundary,
                escalated_at=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

    monkeypatch.setattr(db_config, "engine", engine)
    from app.main import _bootstrap_resume_manual_action_tasks_after_startup

    _bootstrap_resume_manual_action_tasks_after_startup()

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-09-CAPTCHA")
        ).all()
        assert len(tasks) == 2
        new_task = [t for t in tasks if t.task_id != "original-captcha-task"][0]
        assert new_task.status == TaskStatus.dispatched


def test_resume_manual_action_bootstrap_is_a_noop_when_nothing_answered(monkeypatch):
    engine = _make_engine()
    with Session(engine) as session:
        session.add(
            CanonicalOpportunity(
                canonical_opportunity_id="HUNTER-CAND-2026-09-09-CAPTCHA",
                lane="compliance_recovery",
                factual_mechanism="GA unclaimed property",
                source_provenance="seed",
                freshness_date=date(2026, 9, 8),
                disposition=Disposition.pending_commander.value,
                commander_response=None,
                required_commander_checkpoints="[MANUAL-ACTION-NEEDED] hit a CAPTCHA",
            )
        )
        session.commit()

    monkeypatch.setattr(db_config, "engine", engine)
    from app.main import _bootstrap_resume_manual_action_tasks_after_startup

    _bootstrap_resume_manual_action_tasks_after_startup()

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-09-CAPTCHA")
        ).all()
        assert tasks == []
