"""
Regression test for the 2026-09-02 fix: a successful VIP auto-invest
trade previously hit Alpaca directly via httpx with no BudgetAllocation
record at all -- invisible in /budget/allocations, /budget/transactions,
and the scoreboard, even though real money moved. This proves the trade
now gets recorded as a real, attributable allocation.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main  # noqa: F401 — registers all SQLModel tables

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.budget import BudgetAllocation, AllocationCategory, AllocationStatus
from app.services import budget as budget_svc
from app.services import signal_engine as se


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_successful_vip_execution_creates_a_real_allocation_record():
    with _make_session() as session:
        budget_svc.open_weekly_budget(session, starting_budget=200.0)

        vip = {"key": "Trump, Donald J.", "label": "President Trump", "source": "oge_278t", "ticker_override": None}
        raw = {"source": "oge_278t", "source_id": "test-alloc-1", "ticker": "DJT"}
        exec_result = {"status": "executed", "ticker": "DJT", "amount": 3.70, "alpaca_order_id": "test-order-123"}

        se._record_vip_allocation(session, vip, raw, exec_result, confidence=0.37)

        allocations = session.exec(select(BudgetAllocation)).all()
        assert len(allocations) == 1
        alloc = allocations[0]
        assert alloc.amount_allocated == 3.70
        assert alloc.category == AllocationCategory.trading
        assert alloc.status == AllocationStatus.active
        assert "President Trump" in alloc.allocation_name
        assert "DJT" in alloc.allocation_name
        assert alloc.approved_by_commander is True


def test_vip_allocation_rationale_includes_confidence_and_order_id():
    with _make_session() as session:
        budget_svc.open_weekly_budget(session, starting_budget=200.0)

        vip = {"key": "Musk, Elon", "label": "Elon Musk (DOGE)", "source": "oge_278t", "ticker_override": None}
        raw = {"source": "oge_278t", "source_id": "test-alloc-2", "ticker": "TSLA"}
        exec_result = {"status": "executed", "ticker": "TSLA", "amount": 4.20, "alpaca_order_id": "order-456"}

        se._record_vip_allocation(session, vip, raw, exec_result, confidence=0.42)

        alloc = session.exec(select(BudgetAllocation)).first()
        assert "0.42" in alloc.rationale
        assert "order-456" in alloc.rationale


def test_no_allocation_recorded_when_no_open_budget_exists():
    """Should not crash when there's no open budget cycle -- just skip
    recording and log a warning, since the trade already happened either way."""
    with _make_session() as session:
        vip = {"key": "Trump, Donald J.", "label": "President Trump", "source": "oge_278t", "ticker_override": None}
        raw = {"source": "oge_278t", "source_id": "test-alloc-3", "ticker": "DJT"}
        exec_result = {"status": "executed", "ticker": "DJT", "amount": 2.00, "alpaca_order_id": "order-789"}

        se._record_vip_allocation(session, vip, raw, exec_result, confidence=0.20)

        allocations = session.exec(select(BudgetAllocation)).all()
        assert len(allocations) == 0
