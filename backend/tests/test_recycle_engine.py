"""
Tests for recycle_engine service.

Fully deterministic — no real broker calls, no database writes.
All external dependencies are monkey-patched.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Config env defaults ───────────────────────────────────────────────────────
os.environ.setdefault("EXECUTION_MODE", "live")
os.environ.setdefault("ALPACA_ENABLED", "true")
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")
os.environ.setdefault("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
os.environ.setdefault("STRATEGY_MODE", "RECYCLE")
os.environ.setdefault("LIVE_EXECUTION_STRATEGY", "INTRADAY_RECYCLE")
os.environ.setdefault("TARGET_PROFIT_PCT", "0.02")
os.environ.setdefault("STOP_LOSS_PCT", "-0.015")
os.environ.setdefault("MAX_HOLD_MINUTES", "240")
os.environ.setdefault("MAX_OPEN_POSITIONS", "3")
os.environ.setdefault("MAX_POSITION_DOLLARS", "30.0")
os.environ.setdefault("MAX_POSITION_PCT_OF_BANKROLL", "0.30")
os.environ.setdefault("MIN_REQUIRED_BUYING_POWER", "5.0")
os.environ.setdefault("CAPITAL_RESERVE_BUFFER", "2.0")
os.environ.setdefault("REPLACEMENT_RANK_THRESHOLD", "0.15")
os.environ.setdefault("FORCE_SELL_END_OF_DAY", "true")
os.environ.setdefault("ALLOW_OVERNIGHT_HOLD", "false")
os.environ.setdefault("PYRAMIDING_ENABLED", "false")

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_broker_state(
    positions=None,
    buying_power=50.0,
    effective_buying_power=46.0,
    sync_success=True,
    open_buy_orders=None,
    open_sell_orders=None,
    cash=50.0,
    portfolio_value=80.0,
):
    from app.services.broker_reconciliation import (
        BrokerCapitalState, PositionSnapshot, OpenOrderSnapshot
    )
    pos_list = positions or []
    committed = sum(p.market_value for p in pos_list)
    return BrokerCapitalState(
        cash=cash,
        buying_power=buying_power,
        portfolio_value=portfolio_value,
        available_capital=buying_power - 2.0,
        committed_capital=committed,
        current_bankroll=cash + committed,
        unrealized_pl=sum(p.unrealized_pl for p in pos_list),
        unrealized_pl_pct=0.0,
        reserved_by_open_orders=0.0,
        effective_buying_power=effective_buying_power,
        open_positions_count=len(pos_list),
        open_buy_orders_count=len(open_buy_orders or []),
        open_sell_orders_count=len(open_sell_orders or []),
        positions=pos_list,
        open_buy_orders=open_buy_orders or [],
        open_sell_orders=open_sell_orders or [],
        last_sync_at="2026-04-13T12:00:00+00:00",
        broker_mode="live",
        sync_success=sync_success,
        sync_error=None,
    )


def _make_position(symbol, upl_pct=0.0, hold_minutes=30.0, market_value=25.0):
    from app.services.broker_reconciliation import PositionSnapshot
    avg_price = 25.0
    upl = market_value * upl_pct
    return PositionSnapshot(
        symbol=symbol,
        qty=1.0,
        market_value=market_value,
        avg_entry_price=avg_price,
        unrealized_pl=upl,
        unrealized_pl_pct=upl_pct,
        side="long",
        hold_minutes=hold_minutes,
    )


# ── Exit evaluation tests ─────────────────────────────────────────────────────

class TestEvaluateExits:
    """Tests for evaluate_exits()."""

    def test_triggers_on_profit_target(self):
        """Position at or above TARGET_PROFIT_PCT triggers a profit-target exit."""
        from app.services.recycle_engine import evaluate_exits, ExitReason
        import app.services.recycle_engine as re_mod

        orig = re_mod.TARGET_PROFIT_PCT
        try:
            re_mod.TARGET_PROFIT_PCT = 0.02
            pos = _make_position("AAPL", upl_pct=0.025)  # 2.5% > 2% target
            state = _make_broker_state(positions=[pos])
            decisions = evaluate_exits(state)
        finally:
            re_mod.TARGET_PROFIT_PCT = orig

        assert len(decisions) == 1
        assert decisions[0].symbol == "AAPL"
        assert decisions[0].exit_reason == ExitReason.PROFIT_TARGET

    def test_triggers_on_stop_loss(self):
        """Position below STOP_LOSS_PCT triggers a stop-loss exit."""
        from app.services.recycle_engine import evaluate_exits, ExitReason
        import app.services.recycle_engine as re_mod

        orig = re_mod.STOP_LOSS_PCT
        try:
            re_mod.STOP_LOSS_PCT = -0.015
            pos = _make_position("MSFT", upl_pct=-0.02)  # -2% < -1.5% stop
            state = _make_broker_state(positions=[pos])
            decisions = evaluate_exits(state)
        finally:
            re_mod.STOP_LOSS_PCT = orig

        assert len(decisions) == 1
        assert decisions[0].symbol == "MSFT"
        assert decisions[0].exit_reason == ExitReason.STOP_LOSS

    def test_triggers_on_max_hold_time(self):
        """Position held longer than MAX_HOLD_MINUTES triggers a time exit."""
        from app.services.recycle_engine import evaluate_exits, ExitReason
        import app.services.recycle_engine as re_mod

        orig = re_mod.MAX_HOLD_MINUTES
        try:
            re_mod.MAX_HOLD_MINUTES = 60
            pos = _make_position("NVDA", upl_pct=0.005, hold_minutes=90.0)
            state = _make_broker_state(positions=[pos])
            decisions = evaluate_exits(state)
        finally:
            re_mod.MAX_HOLD_MINUTES = orig

        assert len(decisions) == 1
        assert decisions[0].exit_reason == ExitReason.MAX_HOLD_TIME

    def test_no_exit_when_position_is_healthy(self):
        """A profitable position within hold window generates no exit decision."""
        from app.services.recycle_engine import evaluate_exits
        import app.services.recycle_engine as re_mod

        pos = _make_position("AAPL", upl_pct=0.005, hold_minutes=30.0)
        state = _make_broker_state(positions=[pos])
        # Freeze market hours so overnight_prevention never fires during CI
        with patch.object(re_mod, "_is_outside_market_hours", return_value=False),              patch.object(re_mod, "_is_near_market_close", return_value=False):
            decisions = evaluate_exits(state)
        assert decisions == []

    def test_no_exit_on_failed_broker_sync(self):
        """evaluate_exits returns [] when broker sync has failed."""
        from app.services.recycle_engine import evaluate_exits

        state = _make_broker_state(sync_success=False)
        decisions = evaluate_exits(state)
        assert decisions == []

    def test_overnight_prevention_exit(self):
        """Positions should be exited when outside market hours and ALLOW_OVERNIGHT_HOLD=False."""
        from app.services.recycle_engine import evaluate_exits, ExitReason
        import app.services.recycle_engine as re_mod

        pos = _make_position("TSLA", upl_pct=0.001, hold_minutes=5.0)
        state = _make_broker_state(positions=[pos])

        orig_allow = re_mod.ALLOW_OVERNIGHT_HOLD
        try:
            re_mod.ALLOW_OVERNIGHT_HOLD = False
            with patch.object(re_mod, "_is_outside_market_hours", return_value=True):
                decisions = evaluate_exits(state)
        finally:
            re_mod.ALLOW_OVERNIGHT_HOLD = orig_allow

        assert len(decisions) == 1
        assert decisions[0].exit_reason == ExitReason.OVERNIGHT_PREVENTION


# ── Entry evaluation tests ────────────────────────────────────────────────────

class TestEvaluateEntries:
    """Tests for evaluate_entries()."""

    def _candidates(self, symbols_with_conf):
        return [
            {
                "symbol": sym,
                "confidence": conf,
                "score": 75.0,
                "estimated_profit": 20.0,
                "source_id": f"at:{sym.lower()}-001",
                "notes": f"symbol: {sym}",
            }
            for sym, conf in symbols_with_conf
        ]

    def test_approves_entry_when_buying_power_available(self):
        """A healthy broker state approves entries for unblocked candidates."""
        from app.services.recycle_engine import evaluate_entries

        state = _make_broker_state(
            buying_power=80.0, effective_buying_power=78.0, cash=80.0
        )
        candidates = self._candidates([("AAPL", 0.85)])
        entries = evaluate_entries(state, candidates)
        assert len(entries) == 1
        assert entries[0].symbol == "AAPL"
        assert entries[0].notional > 0

    def test_rejects_entry_when_symbol_already_held(self):
        """Symbols already in broker positions are not entered again."""
        from app.services.recycle_engine import evaluate_entries

        pos = _make_position("AAPL")
        state = _make_broker_state(
            positions=[pos], buying_power=80.0, effective_buying_power=50.0
        )
        candidates = self._candidates([("AAPL", 0.85)])
        entries = evaluate_entries(state, candidates)
        assert entries == []

    def test_rejects_entry_when_max_positions_reached(self):
        """No entries when MAX_OPEN_POSITIONS is already at the limit."""
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig = re_mod.MAX_OPEN_POSITIONS
        try:
            re_mod.MAX_OPEN_POSITIONS = 1
            pos = _make_position("MSFT")
            state = _make_broker_state(positions=[pos], effective_buying_power=50.0)
            candidates = self._candidates([("AAPL", 0.85), ("NVDA", 0.80)])
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MAX_OPEN_POSITIONS = orig

        assert entries == []

    def test_rejects_entry_when_insufficient_buying_power(self):
        """No entry when effective buying power is below MIN_REQUIRED_BUYING_POWER."""
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig = re_mod.MIN_REQUIRED_BUYING_POWER
        try:
            re_mod.MIN_REQUIRED_BUYING_POWER = 5.0
            # effective_buying_power = 2 (below minimum)
            state = _make_broker_state(buying_power=4.0, effective_buying_power=2.0, cash=4.0)
            candidates = self._candidates([("AAPL", 0.85)])
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MIN_REQUIRED_BUYING_POWER = orig

        assert entries == []

    def test_entry_notional_capped_at_max_position_dollars(self):
        """Entry notional never exceeds MAX_POSITION_DOLLARS."""
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig = re_mod.MAX_POSITION_DOLLARS
        try:
            re_mod.MAX_POSITION_DOLLARS = 15.0
            state = _make_broker_state(buying_power=100.0, effective_buying_power=98.0, cash=100.0)
            candidates = self._candidates([("AAPL", 0.85)])
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MAX_POSITION_DOLLARS = orig

        assert len(entries) == 1
        assert entries[0].notional <= 15.0

    def test_multiple_candidates_ordered_by_confidence(self):
        """Higher-confidence candidates are entered before lower-confidence ones."""
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig = re_mod.MAX_OPEN_POSITIONS
        try:
            re_mod.MAX_OPEN_POSITIONS = 2
            state = _make_broker_state(buying_power=200.0, effective_buying_power=180.0, cash=200.0)
            candidates = self._candidates([("LOW", 0.65), ("HIGH", 0.92), ("MED", 0.75)])
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MAX_OPEN_POSITIONS = orig

        assert len(entries) == 2
        symbols = [e.symbol for e in entries]
        assert "HIGH" in symbols
        assert "MED" in symbols
        assert "LOW" not in symbols


# ── Replacement evaluation tests ──────────────────────────────────────────────

class TestEvaluateReplacements:
    """Tests for evaluate_replacements()."""

    def test_triggers_replacement_when_slots_full_and_better_candidate(self):
        """Replacement is triggered when positions are full and a much better candidate exists."""
        from app.services.recycle_engine import evaluate_replacements, ExitReason
        import app.services.recycle_engine as re_mod

        orig_max = re_mod.MAX_OPEN_POSITIONS
        orig_thresh = re_mod.REPLACEMENT_RANK_THRESHOLD
        try:
            re_mod.MAX_OPEN_POSITIONS = 2
            re_mod.REPLACEMENT_RANK_THRESHOLD = 0.10
            weak_pos = _make_position("WEAK", upl_pct=-0.005, market_value=20.0)
            strong_pos = _make_position("STRONG", upl_pct=0.01, market_value=25.0)
            state = _make_broker_state(positions=[weak_pos, strong_pos])
            candidates = [{"symbol": "SUPER", "confidence": 0.95, "score": 90.0}]
            decisions = evaluate_replacements(state, candidates)
        finally:
            re_mod.MAX_OPEN_POSITIONS = orig_max
            re_mod.REPLACEMENT_RANK_THRESHOLD = orig_thresh

        assert len(decisions) == 1
        assert decisions[0].exit_reason == ExitReason.REPLACEMENT
        assert decisions[0].symbol == "WEAK"  # weakest by upl_pct

    def test_no_replacement_when_slots_available(self):
        """Replacement is not triggered when there are open position slots."""
        from app.services.recycle_engine import evaluate_replacements
        import app.services.recycle_engine as re_mod

        orig = re_mod.MAX_OPEN_POSITIONS
        try:
            re_mod.MAX_OPEN_POSITIONS = 3
            pos = _make_position("AAPL")
            state = _make_broker_state(positions=[pos])   # only 1 of 3 used
            candidates = [{"symbol": "NVDA", "confidence": 0.99, "score": 99.0}]
            decisions = evaluate_replacements(state, candidates)
        finally:
            re_mod.MAX_OPEN_POSITIONS = orig

        assert decisions == []

    def test_no_replacement_when_candidate_not_significantly_better(self):
        """Replacement is not triggered when new candidate improvement is below threshold."""
        from app.services.recycle_engine import evaluate_replacements
        import app.services.recycle_engine as re_mod

        orig_max = re_mod.MAX_OPEN_POSITIONS
        orig_thresh = re_mod.REPLACEMENT_RANK_THRESHOLD
        try:
            re_mod.MAX_OPEN_POSITIONS = 1
            re_mod.REPLACEMENT_RANK_THRESHOLD = 0.50  # very high threshold
            pos = _make_position("AAPL", upl_pct=0.01)
            state = _make_broker_state(positions=[pos])
            # Candidate with confidence 0.60 — not significantly better
            candidates = [{"symbol": "MSFT", "confidence": 0.60, "score": 60.0}]
            decisions = evaluate_replacements(state, candidates)
        finally:
            re_mod.MAX_OPEN_POSITIONS = orig_max
            re_mod.REPLACEMENT_RANK_THRESHOLD = orig_thresh

        assert decisions == []


# ── Full cycle tests ──────────────────────────────────────────────────────────

class TestRecycleCycle:
    """Integration tests for run_recycle_cycle()."""

    def test_cycle_skipped_when_strategy_not_recycle(self):
        """Cycle returns skipped=True when STRATEGY_MODE != RECYCLE."""
        from app.services.recycle_engine import run_recycle_cycle
        import app.services.recycle_engine as re_mod

        orig = re_mod.STRATEGY_MODE
        try:
            re_mod.STRATEGY_MODE = "ACCUMULATE"
            result = run_recycle_cycle()
        finally:
            re_mod.STRATEGY_MODE = orig

        assert result.cycle_skipped is True

    def test_cycle_exits_positions_before_buying(self):
        """
        End-to-end: a position at profit target is exited first, then a new
        entry is approved with the freed capital.
        """
        from app.services.recycle_engine import run_recycle_cycle
        import app.services.recycle_engine as re_mod

        orig_mode = re_mod.STRATEGY_MODE
        orig_target = re_mod.TARGET_PROFIT_PCT
        orig_allow = re_mod.ALLOW_OVERNIGHT_HOLD

        try:
            re_mod.STRATEGY_MODE = "RECYCLE"
            re_mod.TARGET_PROFIT_PCT = 0.02
            re_mod.ALLOW_OVERNIGHT_HOLD = False

            # Position at profit target
            exitable_pos = _make_position("AAPL", upl_pct=0.025, market_value=25.0)
            pre_exit_state = _make_broker_state(
                positions=[exitable_pos],
                buying_power=10.0, effective_buying_power=8.0,
            )
            # After exit: cash freed, no positions
            post_exit_state = _make_broker_state(
                positions=[],
                buying_power=35.0, effective_buying_power=30.0,
            )

            mock_sell_result = MagicMock()
            mock_sell_result.order_id = "sell_001"
            mock_sell_result.status = "filled"
            mock_sell_result.qty = 1.0

            mock_buy_result = MagicMock()
            mock_buy_result.order_id = "buy_001"
            mock_buy_result.status = "accepted"
            mock_buy_result.qty = 1.0
            mock_buy_result.notional = 20.0

            mock_adapter = MagicMock()
            mock_adapter.get_positions.side_effect = [
                [exitable_pos],  # pre-exit check in execute_exits
                [],              # post-exit positions check
            ]
            mock_adapter.place_order.side_effect = [mock_sell_result, mock_buy_result]

            call_count = [0]

            def _mock_broker_state(*args, **kwargs):
                n = call_count[0]
                call_count[0] += 1
                if n == 0:
                    return pre_exit_state
                return post_exit_state

            candidate = {
                "symbol": "NVDA",
                "confidence": 0.88,
                "score": 80.0,
                "estimated_profit": 20.0,
                "source_id": "at:nvda-001",
                "notes": "symbol: NVDA",
            }

            with (
                patch.object(re_mod, "get_broker_capital_state", side_effect=_mock_broker_state),
                patch.object(re_mod, "cancel_stale_buy_orders", return_value=[]),
                patch.object(re_mod, "_load_live_candidates", return_value=[candidate]),
                patch.object(re_mod, "_is_outside_market_hours", return_value=False),
                patch.object(re_mod, "_is_near_market_close", return_value=False),
                patch("app.services.recycle_engine.get_alpaca_adapter", return_value=mock_adapter),
                patch("app.services.broker_reconciliation.get_alpaca_adapter", return_value=mock_adapter),
            ):
                from app.services.broker_reconciliation import broker_capital_state_to_dict as _bctd
                with patch("app.services.recycle_engine.broker_capital_state_to_dict", side_effect=_bctd):
                    result = run_recycle_cycle()

        finally:
            re_mod.STRATEGY_MODE = orig_mode
            re_mod.TARGET_PROFIT_PCT = orig_target
            re_mod.ALLOW_OVERNIGHT_HOLD = orig_allow

        assert result.cycle_skipped is False
        assert result.exits_submitted >= 1
        assert result.entries_submitted >= 1

    def test_no_entries_when_buying_power_insufficient(self):
        """No buy orders are placed when effective buying power is below minimum."""
        from app.services.recycle_engine import run_recycle_cycle
        import app.services.recycle_engine as re_mod

        orig_mode = re_mod.STRATEGY_MODE
        orig_min_bp = re_mod.MIN_REQUIRED_BUYING_POWER

        try:
            re_mod.STRATEGY_MODE = "RECYCLE"
            re_mod.MIN_REQUIRED_BUYING_POWER = 10.0

            # Account with almost no buying power
            low_bp_state = _make_broker_state(
                buying_power=4.0, effective_buying_power=2.0, cash=4.0
            )

            candidate = {
                "symbol": "AAPL", "confidence": 0.90, "score": 85.0,
                "estimated_profit": 20.0, "source_id": "at:aapl-001", "notes": "symbol: AAPL",
            }

            with (
                patch.object(re_mod, "get_broker_capital_state", return_value=low_bp_state),
                patch.object(re_mod, "cancel_stale_buy_orders", return_value=[]),
                patch.object(re_mod, "_load_live_candidates", return_value=[candidate]),
                patch.object(re_mod, "_is_outside_market_hours", return_value=False),
                patch.object(re_mod, "_is_near_market_close", return_value=False),
                patch("app.services.recycle_engine.broker_capital_state_to_dict",
                      return_value={"buying_power": 2.0}),
            ):
                result = run_recycle_cycle()

        finally:
            re_mod.STRATEGY_MODE = orig_mode
            re_mod.MIN_REQUIRED_BUYING_POWER = orig_min_bp

        assert result.entries_submitted == 0
        assert len(result.warnings) > 0
        assert any("buying power" in w.lower() for w in result.warnings)


# ── Dashboard capital state reconciliation test ───────────────────────────────

class TestDashboardCapitalState:
    """
    Tests verifying that the broker-reconciled capital state used by the
    dashboard correctly shows broker truth over internal ledger values.
    """

    def test_broker_values_win_over_internal_in_live_mode(self):
        """
        In live mode, the returned capital state should use broker buying_power
        for available_capital, not the stale internal ledger value.
        """
        from app.services.broker_reconciliation import (
            BrokerCapitalState, broker_capital_state_to_dict
        )

        # Simulate: internal ledger still says $100 available (seed bankroll)
        # but broker says only $2.17 buying power (capital is deployed)
        broker_state = BrokerCapitalState(
            cash=2.17,
            buying_power=2.17,
            portfolio_value=27.17,
            available_capital=0.17,  # 2.17 - 2.0 buffer
            committed_capital=25.0,  # open positions
            current_bankroll=27.17,
            unrealized_pl=-0.30,
            unrealized_pl_pct=-0.012,
            reserved_by_open_orders=0.0,
            effective_buying_power=0.17,
            open_positions_count=1,
            open_buy_orders_count=0,
            open_sell_orders_count=0,
            last_sync_at="2026-04-13T12:00:00+00:00",
            broker_mode="live",
            sync_success=True,
            sync_error=None,
            internal_available_capital=100.0,   # stale internal
            internal_committed_capital=0.0,
            internal_current_bankroll=100.0,
            mismatch_detected=True,
            mismatch_details="available_capital: broker=$0.17 vs internal=$100.00 (Δ$99.83)",
        )

        result = broker_capital_state_to_dict(broker_state)

        # Dashboard should show broker values, not internal $100
        assert result["available_capital"] == 0.17
        assert result["committed_capital"] == 25.0
        assert result["current_bankroll"] == 27.17
        assert result["unrealized_pl"] == -0.30
        assert result["mismatch_detected"] is True
        assert result["funded_packets"] == 1   # open_positions_count alias

        # Internal values should still be present for audit
        assert result["internal_available_capital"] == 100.0

    def test_no_entries_while_capital_tied_up_in_positions(self):
        """
        When broker shows capital is tied up (buying_power near zero due to
        open positions), evaluate_entries must return no new buy decisions.
        """
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig_min = re_mod.MIN_REQUIRED_BUYING_POWER
        try:
            re_mod.MIN_REQUIRED_BUYING_POWER = 5.0
            # Broker has $2.17 buying power — cannot satisfy $5 minimum
            state = _make_broker_state(
                positions=[_make_position("AAPL", market_value=25.0)],
                buying_power=2.17,
                effective_buying_power=0.17,
                cash=2.17,
            )
            candidates = [
                {"symbol": "NVDA", "confidence": 0.92, "score": 88.0,
                 "estimated_profit": 20.0, "source_id": "at:nvda", "notes": "symbol: NVDA"},
            ]
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MIN_REQUIRED_BUYING_POWER = orig_min

        assert entries == []

    def test_entries_allowed_after_capital_freed(self):
        """After a sell is processed and buying power returns, entries are approved."""
        from app.services.recycle_engine import evaluate_entries
        import app.services.recycle_engine as re_mod

        orig_min = re_mod.MIN_REQUIRED_BUYING_POWER
        orig_max = re_mod.MAX_POSITION_DOLLARS
        try:
            re_mod.MIN_REQUIRED_BUYING_POWER = 5.0
            re_mod.MAX_POSITION_DOLLARS = 20.0
            # After sell: buying power restored to $35
            state = _make_broker_state(
                positions=[],
                buying_power=35.0,
                effective_buying_power=33.0,
                cash=35.0,
            )
            candidates = [
                {"symbol": "NVDA", "confidence": 0.88, "score": 80.0,
                 "estimated_profit": 20.0, "source_id": "at:nvda", "notes": "symbol: NVDA"},
            ]
            entries = evaluate_entries(state, candidates)
        finally:
            re_mod.MIN_REQUIRED_BUYING_POWER = orig_min
            re_mod.MAX_POSITION_DOLLARS = orig_max

        assert len(entries) == 1
        assert entries[0].symbol == "NVDA"
        assert entries[0].notional <= 20.0
