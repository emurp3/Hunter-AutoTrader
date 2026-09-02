"""
Regression tests for two live-bugs found and fixed 2026-09-02:

1. open_weekly_budget() defaulted to the static HUNTER_INITIAL_BANKROLL config
   value (e.g. $500) instead of the real broker cash balance when no
   starting_budget was given, silently seeding a new week with money that
   was never actually deposited.

2. get_broker_reconciled_capital_state()'s nested "broker" dict was snapshotted
   from broker_state BEFORE the reconciliation block corrected
   internal_available_capital / internal_committed_capital /
   internal_current_bankroll / mismatch_detected, so the nested object stayed
   permanently stale (mismatch_detected=True forever) even after a
   successful reconcile.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import budget as budget_svc


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_open_weekly_budget_uses_live_broker_cash_when_alpaca_enabled(monkeypatch):
    monkeypatch.setattr(budget_svc, "ALPACA_ENABLED", True)
    monkeypatch.setattr(budget_svc, "WEEKLY_BUDGET", 500.0)

    fake_account = SimpleNamespace(cash=161.66)
    monkeypatch.setattr(
        "app.integration.brokerage.alpaca.get_alpaca_adapter",
        lambda: SimpleNamespace(get_account=lambda: fake_account),
    )

    with _make_session() as session:
        bankroll = budget_svc.open_weekly_budget(session, starting_budget=None)
        assert bankroll.starting_bankroll == 161.66
        assert bankroll.starting_budget == 161.66
        assert bankroll.current_bankroll == 161.66


def test_open_weekly_budget_falls_back_to_config_when_broker_lookup_fails(monkeypatch):
    monkeypatch.setattr(budget_svc, "ALPACA_ENABLED", True)
    monkeypatch.setattr(budget_svc, "WEEKLY_BUDGET", 500.0)

    def _boom():
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr("app.integration.brokerage.alpaca.get_alpaca_adapter", _boom)

    with _make_session() as session:
        bankroll = budget_svc.open_weekly_budget(session, starting_budget=None)
        assert bankroll.starting_bankroll == 500.0


def test_open_weekly_budget_respects_explicit_amount_even_with_alpaca_enabled(monkeypatch):
    monkeypatch.setattr(budget_svc, "ALPACA_ENABLED", True)
    monkeypatch.setattr(budget_svc, "WEEKLY_BUDGET", 500.0)
    monkeypatch.setattr(
        "app.integration.brokerage.alpaca.get_alpaca_adapter",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    with _make_session() as session:
        bankroll = budget_svc.open_weekly_budget(session, starting_budget=250.0)
        assert bankroll.starting_bankroll == 250.0


def test_broker_dict_reflects_corrected_mismatch_after_reconcile(monkeypatch):
    """
    Simulates the exact scenario from the live incident: internal ledger
    shows $0.01 available / mismatch before reconcile; broker sync succeeds
    and reports the true $159.66 available. The nested "broker" dict in the
    response must reflect the corrected, post-sync values -- not the stale
    pre-sync snapshot.
    """
    monkeypatch.setattr(budget_svc, "ALPACA_ENABLED", True)

    with _make_session() as session:
        bankroll = budget_svc.open_weekly_budget(session, starting_budget=163.0)
        # Simulate the stale internal ledger: nearly everything "committed".
        bankroll.remaining_budget = 0.01
        bankroll.current_bankroll = 163.0
        session.add(bankroll)
        session.commit()

        from app.services import broker_reconciliation as recon_svc

        fake_broker_state = recon_svc.BrokerCapitalState(
            cash=161.66,
            buying_power=161.66,
            portfolio_value=161.66,
            available_capital=159.66,
            committed_capital=0.0,
            current_bankroll=161.66,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
            reserved_by_open_orders=0.0,
            effective_buying_power=159.66,
            open_positions_count=0,
            open_buy_orders_count=0,
            open_sell_orders_count=0,
            sync_success=True,
            broker_mode="live",
            # Pre-sync internal values -- exactly what get_broker_capital_state()
            # would have been called with, before this function corrects them.
            internal_available_capital=0.01,
            internal_committed_capital=162.99,
            internal_current_bankroll=163.0,
            mismatch_detected=True,
            mismatch_details="stale mismatch from before reconcile",
        )
        monkeypatch.setattr(recon_svc, "get_broker_capital_state", lambda **kwargs: fake_broker_state)

        from app.services import position_lifecycle as lifecycle_svc
        from app.services import execution as execution_svc

        monkeypatch.setattr(lifecycle_svc, "sync_lifecycles_with_broker_state", lambda *a, **k: None)
        monkeypatch.setattr(lifecycle_svc, "reconcile_order_fills_with_broker", lambda *a, **k: None)
        monkeypatch.setattr(lifecycle_svc, "enrich_broker_positions_with_lifecycle", lambda session, positions: positions)
        monkeypatch.setattr(execution_svc, "reconcile_completed_packet_outcomes", lambda *a, **k: None)

        result = budget_svc.get_broker_reconciled_capital_state(session)

        # Top-level values were already correct before this fix.
        assert result["available_capital"] == 159.66
        assert result["mismatch_detected"] is False

        # This is the actual bug: the nested "broker" object must now match,
        # not stay frozen at the stale pre-reconcile snapshot.
        assert result["broker"]["internal_available_capital"] == 159.66
        assert result["broker"]["internal_committed_capital"] == 0.0
        assert result["broker"]["mismatch_detected"] is False
        assert result["broker"]["mismatch_details"] is None
