"""
Recovery-board decision (Commander, 2026-09-10): crypto_engine.place_crypto_order()
is real, live-money Alpaca code with its own hard 15% portfolio-cap wall,
but was never wired into any unattended/scheduled path — only reachable
via an authenticated API call. ENABLE_CRYPTO_AUTO_INVEST is the new
unattended-activation flag, default OFF, mirroring ENABLE_VIP_AUTO_INVEST.
No new sizing/cap is introduced — this reuses crypto_engine's own default
notional (CRYPTO_MICRO_INVEST) and its existing hard-wall enforcement.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main  # noqa: F401 — registers all SQLModel tables

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import ENABLE_CRYPTO_AUTO_INVEST
from app.models.budget import BudgetAllocation, AllocationCategory, AllocationStatus
from app.services import budget as budget_svc
from app.services import signal_engine as se


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_crypto_auto_invest_defaults_off():
    assert ENABLE_CRYPTO_AUTO_INVEST is False


def test_execute_crypto_auto_invest_calls_place_crypto_order(monkeypatch):
    calls = []

    def _fake_place_crypto_order(symbol, side="buy", notional=None):
        calls.append({"symbol": symbol, "side": side, "notional": notional})
        return {"status": "executed", "symbol": symbol, "notional": 10.0, "side": side, "order_id": "ord-1"}

    import app.services.crypto_engine as crypto_engine
    monkeypatch.setattr(crypto_engine, "place_crypto_order", _fake_place_crypto_order)

    result = se._execute_crypto_auto_invest("BTC", "buy")

    assert result["status"] == "executed"
    assert calls == [{"symbol": "BTC", "side": "buy", "notional": None}]


def test_execute_crypto_auto_invest_skips_when_no_ticker():
    result = se._execute_crypto_auto_invest("", "buy")
    assert result == {"status": "skip", "reason": "no_ticker"}


def test_execute_crypto_auto_invest_never_invents_a_new_amount(monkeypatch):
    """It must call place_crypto_order() with no notional override, letting
    that function's own MICRO_AMOUNT/hard-cap logic decide sizing — never
    a new number introduced at the signal_engine layer."""
    captured = {}

    def _fake_place_crypto_order(symbol, side="buy", notional=None):
        captured["notional"] = notional
        return {"status": "executed", "symbol": symbol, "notional": 10.0}

    import app.services.crypto_engine as crypto_engine
    monkeypatch.setattr(crypto_engine, "place_crypto_order", _fake_place_crypto_order)

    se._execute_crypto_auto_invest("ETH", "buy")

    assert captured["notional"] is None


def test_successful_crypto_execution_creates_a_real_allocation_record():
    with _make_session() as session:
        budget_svc.open_weekly_budget(session, starting_budget=200.0)

        raw = {"source": "coingecko_velocity", "source_id": "crypto-1", "ticker": "BTC"}
        exec_result = {"status": "executed", "symbol": "BTC", "notional": 10.0, "order_id": "order-crypto-1"}

        se._record_crypto_allocation(session, raw, exec_result, confidence=0.72)

        allocations = session.exec(select(BudgetAllocation)).all()
        assert len(allocations) == 1
        alloc = allocations[0]
        assert alloc.amount_allocated == 10.0
        assert alloc.category == AllocationCategory.trading
        assert alloc.status == AllocationStatus.active
        assert "BTC" in alloc.allocation_name
        assert alloc.approved_by_commander is True
        assert "0.72" in alloc.rationale
        assert "order-crypto-1" in alloc.rationale


def test_no_crypto_allocation_recorded_when_no_open_budget_exists():
    with _make_session() as session:
        raw = {"source": "coingecko_velocity", "source_id": "crypto-2", "ticker": "ETH"}
        exec_result = {"status": "executed", "symbol": "ETH", "notional": 8.0, "order_id": "order-crypto-2"}

        se._record_crypto_allocation(session, raw, exec_result, confidence=0.55)

        allocations = session.exec(select(BudgetAllocation)).all()
        assert len(allocations) == 0
