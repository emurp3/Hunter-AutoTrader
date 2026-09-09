"""
Tests for the Hunter AI chat system-prompt grounding fix.

Production defect: Commander asked Hunter AI "what LLM are you" and "who is
president" and got "I'm based on GPT-3, trained through October 2023" — the
model (gpt-4o, unchanged) hallucinating its own identity and answering a
current-facts question from stale training knowledge, because the prompt
never grounded it in the real date or told it its own self-knowledge isn't
authoritative. These tests check the fix: the prompt now carries the current
date/time, an explicit instruction not to claim a specific model identity,
an explicit instruction not to trust its own training data for current
facts, and Hunter's real execution/queue state — without touching the model
call itself, execution accounting, or trading behavior.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.integration.brokerage.base import AccountInfo
from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.routers.assistant import _build_system_prompt, _gather_context


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.budget  # noqa: F401
    import app.models.execution_outcome  # noqa: F401
    import app.models.strategy  # noqa: F401
    import app.models.copy_signal  # noqa: F401
    import app.models.forge  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_prompt_carries_real_current_datetime_not_a_static_string():
    session = _make_session()
    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)
    assert ctx["current_datetime_utc"] in prompt
    assert str(date.today().year) in ctx["current_datetime_utc"]


def test_prompt_instructs_against_claiming_a_specific_model_identity():
    session = _make_session()
    prompt = _build_system_prompt(_gather_context(session))
    assert "do not describe yourself by a specific model name" in prompt.lower()


def test_prompt_instructs_against_trusting_training_data_for_current_facts():
    session = _make_session()
    prompt = _build_system_prompt(_gather_context(session))
    lowered = prompt.lower()
    assert "not authoritative" in lowered
    assert "research engine" in lowered


def test_prompt_includes_execution_quota_state():
    session = _make_session()
    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)
    assert "EXECUTIONS" in prompt
    assert "0/5 today" in prompt  # no executions recorded in a fresh DB


def test_prompt_includes_active_ledger_queue_when_present():
    session = _make_session()
    session.add(
        CanonicalOpportunity(
            canonical_opportunity_id="TEST-CAND-1",
            lane="compliance_recovery",
            factual_mechanism="test mechanism for prompt grounding",
            source_provenance="unit test",
            freshness_date=date(2026, 9, 8),
            disposition=Disposition.watchlist.value,
        )
    )
    session.commit()

    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)
    assert "TEST-CAND-1" in prompt
    assert "test mechanism for prompt grounding" in prompt


def test_prompt_does_not_crash_with_no_ledger_data():
    session = _make_session()
    ctx = _gather_context(session)
    # Fresh DB: no candidates, no executions — must render safely.
    prompt = _build_system_prompt(ctx)
    assert "No active execution-ledger candidates right now." in prompt
    assert "None open right now." in prompt


def test_gather_context_never_raises_even_if_quota_lookup_fails(monkeypatch):
    session = _make_session()
    import app.services.execution_accounting as acct

    def _boom(*a, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(acct, "get_quota_status", _boom)
    ctx = _gather_context(session)  # must not raise
    assert ctx["quota"] is None
    prompt = _build_system_prompt(ctx)  # must still render
    assert "unavailable" in prompt


# ── Alpaca account-balance fix ──────────────────────────────────────────
# Production defect: this block imported a nonexistent AlpacaClient /
# .get_account() (neither ever existed in this codebase), so it always
# raised and silently fell back to "unknown" — with no log line, unlike
# every other context-fetch block here. Fixed to use the same
# get_alpaca_adapter().get_balance() pattern already used everywhere else
# in the app (execution.py, position_lifecycle.py, recycle_engine.py,
# budget.py). Read-only: no order placement, no risk-control code touched.


def test_account_balance_populated_from_real_adapter_call():
    session = _make_session()
    fake_account = AccountInfo(
        account_id="acct-1", cash=1234.56, portfolio_value=5000.0,
        buying_power=2500.0, status="ACTIVE",
    )
    with patch(
        "app.integration.brokerage.alpaca.get_alpaca_adapter",
        return_value=type("A", (), {"get_balance": lambda self: fake_account})(),
    ):
        ctx = _gather_context(session)
    assert ctx["account_cash"] == "1234.56"
    assert ctx["buying_power"] == "2500.0"
    assert ctx["account_status"] == "ACTIVE"


def test_account_balance_falls_back_to_unknown_and_logs_on_failure(caplog):
    session = _make_session()
    with patch(
        "app.integration.brokerage.alpaca.get_alpaca_adapter",
        side_effect=RuntimeError("simulated Alpaca outage"),
    ):
        with caplog.at_level("WARNING"):
            ctx = _gather_context(session)
    assert ctx["account_cash"] == "unknown"
    assert any("Failed to fetch Alpaca account balance" in r.message for r in caplog.records)
