"""
Tests for broker_reconciliation service.

These tests are fully deterministic — no real broker calls are made.
All Alpaca interactions are monkey-patched with mock objects.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow imports from the backend app package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Minimal stubs for config imports ─────────────────────────────────────────
# Patch config constants before importing the module under test
os.environ.setdefault("EXECUTION_MODE", "live")
os.environ.setdefault("ALPACA_ENABLED", "true")
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")
os.environ.setdefault("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
os.environ.setdefault("CAPITAL_RESERVE_BUFFER", "2.0")
os.environ.setdefault("MIN_REQUIRED_BUYING_POWER", "5.0")
os.environ.setdefault("STALE_ORDER_TIMEOUT_SECONDS", "120")

import pytest


# ── Mock data builders ────────────────────────────────────────────────────────

def _mock_account(cash=50.0, buying_power=48.0, portfolio_value=98.0):
    a = MagicMock()
    a.cash = cash
    a.buying_power = buying_power
    a.portfolio_value = portfolio_value
    a.currency = "USD"
    a.status = "ACTIVE"
    a.id = "test_acct_001"
    return a


def _mock_position(symbol, qty=1.0, market_value=25.0, avg_entry_price=24.5, unrealized_pl=0.5):
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.market_value = market_value
    p.avg_entry_price = avg_entry_price
    p.unrealized_pl = unrealized_pl
    p.side = MagicMock()
    p.side.value = "long"
    p.raw = None
    return p


def _mock_order(order_id, symbol, side="buy", status="new", qty=1.0, notional=None, submitted_at=None):
    o = MagicMock()
    o.id = order_id
    o.symbol = symbol
    o.side = MagicMock()
    o.side.value = side
    o.status = MagicMock()
    o.status.value = status
    o.qty = qty
    o.notional = notional
    o.filled_qty = 0.0
    o.filled_avg_price = None
    o.submitted_at = submitted_at
    o.raw = None
    return o


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGetBrokerCapitalState:
    """Tests for get_broker_capital_state()."""

    def _make_adapter(self, positions=None, orders=None, cash=50.0, buying_power=48.0):
        adapter = MagicMock()
        account = MagicMock()
        account.cash = cash
        account.buying_power = buying_power
        account.portfolio_value = cash + sum(
            float(p.market_value) for p in (positions or [])
        )
        account.currency = "USD"
        account.status = "ACTIVE"
        account.id = "mock_001"
        adapter.get_balance.return_value = account
        adapter.get_positions.return_value = positions or []
        adapter.list_orders.return_value = orders or []
        return adapter

    def test_no_positions_no_orders(self):
        """With an empty account, all capital is available."""
        from app.services.broker_reconciliation import get_broker_capital_state

        adapter = self._make_adapter(positions=[], orders=[], cash=100.0, buying_power=100.0)
        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            state = get_broker_capital_state()

        assert state.sync_success is True
        assert state.cash == 100.0
        assert state.committed_capital == 0.0
        assert state.open_positions_count == 0
        # available_capital = buying_power - buffer = 100 - 2 = 98
        assert state.available_capital == 98.0
        assert state.unrealized_pl == 0.0

    def test_with_open_positions_committed_capital_reflects_broker(self):
        """Committed capital equals sum of market values from broker positions."""
        from app.services.broker_reconciliation import get_broker_capital_state

        pos1 = _mock_position("AAPL", qty=0.5, market_value=20.0, unrealized_pl=0.5)
        pos2 = _mock_position("MSFT", qty=0.3, market_value=10.0, unrealized_pl=-0.2)
        adapter = self._make_adapter(
            positions=[pos1, pos2], orders=[], cash=70.0, buying_power=60.0
        )
        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            state = get_broker_capital_state()

        assert state.sync_success is True
        assert state.committed_capital == 30.0        # 20 + 10
        assert state.unrealized_pl == round(0.5 + (-0.2), 2)  # 0.3
        assert state.open_positions_count == 2
        assert len(state.positions) == 2

    def test_available_capital_deducted_by_reserve_buffer(self):
        """available_capital = buying_power - CAPITAL_RESERVE_BUFFER."""
        from app.services.broker_reconciliation import get_broker_capital_state
        import app.services.broker_reconciliation as br_mod

        orig_buf = br_mod.CAPITAL_RESERVE_BUFFER
        try:
            br_mod.CAPITAL_RESERVE_BUFFER = 2.0
            adapter = self._make_adapter(cash=50.0, buying_power=20.0)
            with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
                state = get_broker_capital_state()
            assert state.available_capital == 18.0   # 20 - 2
        finally:
            br_mod.CAPITAL_RESERVE_BUFFER = orig_buf

    def test_mismatch_detected_when_broker_differs_from_internal(self):
        """Mismatch flag is set when broker available_capital differs from internal by > $1."""
        from app.services.broker_reconciliation import get_broker_capital_state

        adapter = self._make_adapter(cash=50.0, buying_power=50.0)
        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            state = get_broker_capital_state(
                internal_available_capital=100.0,   # internal says $100 available
                internal_committed_capital=0.0,
                internal_current_bankroll=100.0,
            )

        assert state.mismatch_detected is True
        assert state.mismatch_details is not None
        assert "available_capital" in state.mismatch_details

    def test_no_mismatch_when_values_are_close(self):
        """No mismatch when internal and broker values are within $1."""
        from app.services.broker_reconciliation import get_broker_capital_state

        # buying_power=50, buffer=2 → available=48
        adapter = self._make_adapter(cash=50.0, buying_power=50.0)
        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            state = get_broker_capital_state(
                internal_available_capital=48.0,   # matches broker available
                internal_committed_capital=0.0,
                internal_current_bankroll=50.0,
            )

        assert state.mismatch_detected is False

    def test_sync_fails_gracefully_on_adapter_error(self):
        """Adapter errors return sync_success=False and do not raise."""
        from app.services.broker_reconciliation import get_broker_capital_state

        with patch("app.services.broker_reconciliation.get_alpaca_adapter",
                   side_effect=Exception("network error")):
            state = get_broker_capital_state()

        assert state.sync_success is False
        assert "network error" in (state.sync_error or "")

    def test_effective_buying_power_deducted_by_open_buy_orders(self):
        """effective_buying_power = available_capital - reserved_by_open_orders."""
        from app.services.broker_reconciliation import get_broker_capital_state
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        order = _mock_order("ord1", "AAPL", side="buy", status="new", notional=10.0,
                            submitted_at=now)
        order.side.value = "buy"
        order.status.value = "new"

        adapter = self._make_adapter(cash=50.0, buying_power=48.0, orders=[order])

        # Patch _map_result so list_orders returns proper TradeResult objects
        from app.integration.brokerage.base import TradeResult
        def _make_tr(o):
            return TradeResult(
                order_id=str(o.id), symbol=o.symbol, qty=float(o.qty or 0),
                side="buy", status="new", notional=10.0, submitted_at=now.isoformat(),
            )
        adapter.list_orders.return_value = [_make_tr(order)]

        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            state = get_broker_capital_state()

        # available = 48 - 2 = 46, effective = 46 - 10 = 36
        assert state.reserved_by_open_orders == 10.0
        assert state.effective_buying_power == 36.0


class TestCancelStaleBuyOrders:
    """Tests for cancel_stale_buy_orders()."""

    def test_cancels_orders_older_than_timeout(self):
        """Orders older than STALE_ORDER_TIMEOUT_SECONDS are cancelled."""
        from datetime import datetime, timezone, timedelta
        from app.services.broker_reconciliation import cancel_stale_buy_orders
        import app.services.broker_reconciliation as br_mod
        from app.integration.brokerage.base import TradeResult

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        stale_order = TradeResult(
            order_id="stale_1", symbol="AAPL", qty=1.0, side="buy",
            status="new", submitted_at=old_time
        )

        adapter = MagicMock()
        adapter.list_orders.return_value = [stale_order]
        adapter.cancel_order.return_value = True

        orig_timeout = br_mod.STALE_ORDER_TIMEOUT_SECONDS
        try:
            br_mod.STALE_ORDER_TIMEOUT_SECONDS = 120
            with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
                cancelled = cancel_stale_buy_orders()
        finally:
            br_mod.STALE_ORDER_TIMEOUT_SECONDS = orig_timeout

        assert "stale_1" in cancelled
        adapter.cancel_order.assert_called_once_with("stale_1")

    def test_does_not_cancel_fresh_orders(self):
        """Orders newer than timeout threshold are left alone."""
        from datetime import datetime, timezone
        from app.services.broker_reconciliation import cancel_stale_buy_orders
        from app.integration.brokerage.base import TradeResult

        fresh_order = TradeResult(
            order_id="fresh_1", symbol="MSFT", qty=1.0, side="buy",
            status="new", submitted_at=datetime.now(timezone.utc).isoformat()
        )

        adapter = MagicMock()
        adapter.list_orders.return_value = [fresh_order]
        adapter.cancel_order.return_value = True

        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            cancelled = cancel_stale_buy_orders()

        assert cancelled == []
        adapter.cancel_order.assert_not_called()

    def test_does_not_cancel_sell_orders(self):
        """Only buy orders are cancelled for staleness; sell orders are ignored."""
        from datetime import datetime, timezone, timedelta
        from app.services.broker_reconciliation import cancel_stale_buy_orders
        from app.integration.brokerage.base import TradeResult

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        old_sell = TradeResult(
            order_id="sell_1", symbol="AAPL", qty=1.0, side="sell",
            status="new", submitted_at=old_time
        )

        adapter = MagicMock()
        adapter.list_orders.return_value = [old_sell]

        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            cancelled = cancel_stale_buy_orders()

        assert cancelled == []

    def test_returns_empty_when_alpaca_disabled(self):
        """Returns [] immediately when ALPACA_ENABLED=False."""
        import app.services.broker_reconciliation as br_mod
        orig = br_mod.ALPACA_ENABLED
        try:
            br_mod.ALPACA_ENABLED = False
            from app.services.broker_reconciliation import cancel_stale_buy_orders
            result = cancel_stale_buy_orders()
        finally:
            br_mod.ALPACA_ENABLED = orig

        assert result == []


class TestIsSymbolHeld:
    """Tests for is_symbol_held()."""

    def test_returns_true_when_position_exists(self):
        from app.services.broker_reconciliation import is_symbol_held
        from app.integration.brokerage.base import PositionInfo

        pos = PositionInfo(symbol="AAPL", qty=0.5)
        adapter = MagicMock()
        adapter.get_positions.return_value = [pos]

        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            assert is_symbol_held("AAPL") is True
            assert is_symbol_held("aapl") is True   # case-insensitive

    def test_returns_false_when_not_held(self):
        from app.services.broker_reconciliation import is_symbol_held

        adapter = MagicMock()
        adapter.get_positions.return_value = []

        with patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=adapter):
            assert is_symbol_held("AAPL") is False
