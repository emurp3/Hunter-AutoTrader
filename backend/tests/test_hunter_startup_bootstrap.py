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
from datetime import date

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


def _seed(engine, *, ucp_commander_response="Murphy Enterprises LLC", google_disposition=Disposition.watchlist.value):
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
                commander_response="Approved — proceed." if google_disposition != Disposition.pending_commander.value else None,
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


def test_google_reopen_does_not_repeat_once_pending_commander(monkeypatch):
    engine = _make_engine()
    _seed(engine, google_disposition=Disposition.pending_commander.value)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        google = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-02-GOOGLE"
            )
        ).first()
        # Untouched — no [disposition=...] note appended by a redundant reopen.
        assert not (google.next_action or "").count("[disposition=pending_commander]") > 0


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
    _seed(engine, google_disposition=Disposition.pending_commander.value)  # reopened, not yet answered
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
            )
        )
        session.commit()

    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)
    _run_bootstrap(engine, monkeypatch)

    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.source_id == "HUNTER-CAND-2026-09-08-02-GOOGLE")).all()
        assert len(tasks) == 1
