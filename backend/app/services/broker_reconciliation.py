"""
Broker reconciliation service.

In live mode, Hunter's internal ledger (WeeklyBudget / BudgetAllocation) can
drift from the actual broker account state. This module provides the
authoritative capital-state snapshot by querying Alpaca directly and
reconciling it against internal records.

Public API
----------
get_broker_capital_state()          → BrokerCapitalState dataclass
cancel_stale_buy_orders()           → list[str]  (cancelled order_ids)
get_live_open_orders()              → list[TradeResult]
is_symbol_held(symbol)              → bool
is_symbol_order_pending(symbol)     → bool
assert_no_buying_power_needed(...)  → bool
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.config import (
    ALPACA_ENABLED,
    ALPACA_PAPER,
    CAPITAL_RESERVE_BUFFER,
    EXECUTION_MODE,
    MIN_REQUIRED_BUYING_POWER,
    STALE_ORDER_TIMEOUT_SECONDS,
)

# Module-level import so tests can patch app.services.broker_reconciliation.get_alpaca_adapter
from app.integration.brokerage.alpaca import get_alpaca_adapter

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class PositionSnapshot:
    symbol: str
    qty: float
    market_value: float
    avg_entry_price: float
    unrealized_pl: float
    unrealized_pl_pct: float       # fraction, e.g. 0.02 = +2 %
    side: str                       # "long" | "short"
    hold_minutes: Optional[float]   # None if entry time unknown
    raw: Optional[dict] = None


@dataclass
class OpenOrderSnapshot:
    order_id: str
    symbol: str
    side: str                       # "buy" | "sell"
    qty: float
    notional: Optional[float]
    status: str
    submitted_at: Optional[datetime]
    age_seconds: float
    is_stale: bool
    raw: Optional[dict] = None


@dataclass
class BrokerCapitalState:
    """Authoritative capital state pulled directly from the live broker."""

    # Raw account values
    cash: float
    buying_power: float
    portfolio_value: float

    # Derived capital positions
    available_capital: float        # buying_power minus reserve buffer
    committed_capital: float        # sum of open position market values
    current_bankroll: float         # cash + market value of all positions
    unrealized_pl: float            # sum across all open positions
    unrealized_pl_pct: float        # as fraction of committed capital

    # Capital reserved by open (unfilled) buy orders — not yet in positions
    reserved_by_open_orders: float

    # True available = buying_power - reserve_buffer - open_buy_order notionals
    effective_buying_power: float

    # Position and order counts
    open_positions_count: int
    open_buy_orders_count: int
    open_sell_orders_count: int

    # Detail lists
    positions: list[PositionSnapshot] = field(default_factory=list)
    open_buy_orders: list[OpenOrderSnapshot] = field(default_factory=list)
    open_sell_orders: list[OpenOrderSnapshot] = field(default_factory=list)

    # Metadata
    last_sync_at: str = ""          # ISO timestamp
    broker_mode: str = ""           # "paper" | "live"
    sync_success: bool = True
    sync_error: Optional[str] = None

    # Mismatch detection vs internal ledger
    internal_available_capital: Optional[float] = None
    internal_committed_capital: Optional[float] = None
    internal_current_bankroll: Optional[float] = None
    mismatch_detected: bool = False
    mismatch_details: Optional[str] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_live_mode() -> bool:
    return EXECUTION_MODE == "live"


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _order_age_seconds(submitted_at: Optional[datetime]) -> float:
    if submitted_at is None:
        return 0.0
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - submitted_at).total_seconds()


def _map_order_snapshot(order_result) -> OpenOrderSnapshot:
    """Convert a TradeResult to an OpenOrderSnapshot."""
    submitted_at: Optional[datetime] = None
    if order_result.submitted_at:
        try:
            if isinstance(order_result.submitted_at, str):
                submitted_at = datetime.fromisoformat(
                    order_result.submitted_at.replace("Z", "+00:00")
                )
            else:
                submitted_at = order_result.submitted_at
        except Exception:
            submitted_at = None

    age = _order_age_seconds(submitted_at)
    is_stale = age >= STALE_ORDER_TIMEOUT_SECONDS

    return OpenOrderSnapshot(
        order_id=order_result.order_id,
        symbol=order_result.symbol,
        side=order_result.side,
        qty=_safe_float(order_result.qty),
        notional=_safe_float(order_result.notional) if order_result.notional else None,
        status=order_result.status,
        submitted_at=submitted_at,
        age_seconds=age,
        is_stale=is_stale,
        raw=order_result.raw,
    )


# ── Core public functions ─────────────────────────────────────────────────────

def get_broker_capital_state(
    *,
    internal_available_capital: Optional[float] = None,
    internal_committed_capital: Optional[float] = None,
    internal_current_bankroll: Optional[float] = None,
) -> BrokerCapitalState:
    """
    Fetch live broker account state and return an authoritative BrokerCapitalState.

    Never raises — returns a failed-sync state with sync_success=False on errors.
    Works in both sandbox (paper) and live mode.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    if not ALPACA_ENABLED:
        return BrokerCapitalState(
            cash=0.0, buying_power=0.0, portfolio_value=0.0,
            available_capital=0.0, committed_capital=0.0,
            current_bankroll=internal_current_bankroll or 0.0,
            unrealized_pl=0.0, unrealized_pl_pct=0.0,
            reserved_by_open_orders=0.0, effective_buying_power=0.0,
            open_positions_count=0, open_buy_orders_count=0, open_sell_orders_count=0,
            last_sync_at=now_iso,
            broker_mode="paper" if ALPACA_PAPER else "live",
            sync_success=False,
            sync_error="ALPACA_ENABLED=false — broker sync disabled",
            internal_available_capital=internal_available_capital,
            internal_committed_capital=internal_committed_capital,
            internal_current_bankroll=internal_current_bankroll,
        )

    try:
        adapter = get_alpaca_adapter()

        # ── Fetch account ─────────────────────────────────────────────────────
        account = adapter.get_balance()
        cash = _safe_float(account.cash)
        buying_power = _safe_float(account.buying_power)
        portfolio_value = _safe_float(account.portfolio_value)

        # ── Fetch positions ────────────────────────────────────────────────────
        raw_positions = adapter.get_positions()
        positions: list[PositionSnapshot] = []
        total_market_value = 0.0
        total_unrealized_pl = 0.0

        for p in raw_positions:
            mv = _safe_float(p.market_value)
            aep = _safe_float(p.avg_entry_price)
            upl = _safe_float(p.unrealized_pl)
            qty = _safe_float(p.qty)
            upl_pct = (upl / (aep * qty)) if aep > 0 and qty > 0 else 0.0

            # Estimate hold minutes from raw if available
            hold_minutes: Optional[float] = None
            if p.raw and isinstance(p.raw, dict):
                entry_ts = p.raw.get("asset_change_at") or p.raw.get("created_at")
                if entry_ts:
                    try:
                        if isinstance(entry_ts, str):
                            entry_dt = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                        else:
                            entry_dt = entry_ts
                        hold_minutes = (
                            datetime.now(timezone.utc) - entry_dt
                        ).total_seconds() / 60.0
                    except Exception:
                        hold_minutes = None

            positions.append(PositionSnapshot(
                symbol=p.symbol,
                qty=qty,
                market_value=mv,
                avg_entry_price=aep,
                unrealized_pl=upl,
                unrealized_pl_pct=round(upl_pct, 6),
                side=p.side or "long",
                hold_minutes=hold_minutes,
                raw=p.raw,
            ))
            total_market_value += mv
            total_unrealized_pl += upl

        # ── Fetch open orders ─────────────────────────────────────────────────
        open_buy_orders: list[OpenOrderSnapshot] = []
        open_sell_orders: list[OpenOrderSnapshot] = []
        reserved_by_open_orders = 0.0

        try:
            all_orders = adapter.list_orders(limit=50)
            open_statuses = {"new", "partially_filled", "accepted", "pending_new", "held"}
            for order in all_orders:
                if order.status not in open_statuses:
                    continue
                snap = _map_order_snapshot(order)
                if snap.side in ("buy", "BUY"):
                    open_buy_orders.append(snap)
                    # Estimate reserved capital from notional or qty*price
                    if snap.notional:
                        reserved_by_open_orders += snap.notional
                    elif snap.qty > 0:
                        # Try to get current price from position list
                        pos_match = next(
                            (pos for pos in positions if pos.symbol == snap.symbol), None
                        )
                        if pos_match and pos_match.avg_entry_price > 0:
                            reserved_by_open_orders += snap.qty * pos_match.avg_entry_price
                else:
                    open_sell_orders.append(snap)
        except Exception as exc:
            logger.warning("broker_reconciliation: could not fetch open orders — %s", exc)

        # ── Derive capital state ──────────────────────────────────────────────
        # committed_capital = money actively deployed:
        #   open position market values + capital reserved by pending buy orders
        committed_capital = round(total_market_value + reserved_by_open_orders, 2)
        # current_bankroll = Alpaca portfolio_value (total equity, the authoritative
        #   number shown in the Alpaca dashboard — cash + long market value)
        current_bankroll = round(portfolio_value, 2)
        unrealized_pl = round(total_unrealized_pl, 2)
        unrealized_pl_pct = (
            round(unrealized_pl / committed_capital, 6) if committed_capital > 0 else 0.0
        )

        # available_capital = buying_power minus the hard reserve buffer
        available_capital = round(
            max(0.0, buying_power - CAPITAL_RESERVE_BUFFER), 2
        )

        # effective_buying_power = available after buffer AND open buy orders
        effective_buying_power = round(
            max(0.0, available_capital - reserved_by_open_orders), 2
        )

        # ── Mismatch detection ────────────────────────────────────────────────
        mismatch_detected = False
        mismatch_parts: list[str] = []

        if internal_available_capital is not None:
            diff = abs(available_capital - internal_available_capital)
            if diff > 1.00:
                mismatch_detected = True
                mismatch_parts.append(
                    f"available_capital: broker=${available_capital:.2f} "
                    f"vs internal=${internal_available_capital:.2f} (Δ${diff:.2f})"
                )

        if internal_committed_capital is not None:
            diff = abs(committed_capital - internal_committed_capital)
            if diff > 1.00:
                mismatch_detected = True
                mismatch_parts.append(
                    f"committed_capital: broker=${committed_capital:.2f} "
                    f"vs internal=${internal_committed_capital:.2f} (Δ${diff:.2f})"
                )

        if internal_current_bankroll is not None:
            diff = abs(current_bankroll - internal_current_bankroll)
            if diff > 1.00:
                mismatch_detected = True
                mismatch_parts.append(
                    f"current_bankroll: broker=${current_bankroll:.2f} "
                    f"vs internal=${internal_current_bankroll:.2f} (Δ${diff:.2f})"
                )

        mismatch_details = "; ".join(mismatch_parts) if mismatch_parts else None

        return BrokerCapitalState(
            cash=round(cash, 2),
            buying_power=round(buying_power, 2),
            portfolio_value=round(portfolio_value, 2),
            available_capital=available_capital,
            committed_capital=committed_capital,
            current_bankroll=current_bankroll,
            unrealized_pl=unrealized_pl,
            unrealized_pl_pct=unrealized_pl_pct,
            reserved_by_open_orders=round(reserved_by_open_orders, 2),
            effective_buying_power=effective_buying_power,
            open_positions_count=len(positions),
            open_buy_orders_count=len(open_buy_orders),
            open_sell_orders_count=len(open_sell_orders),
            positions=positions,
            open_buy_orders=open_buy_orders,
            open_sell_orders=open_sell_orders,
            last_sync_at=now_iso,
            broker_mode="paper" if ALPACA_PAPER else "live",
            sync_success=True,
            sync_error=None,
            internal_available_capital=internal_available_capital,
            internal_committed_capital=internal_committed_capital,
            internal_current_bankroll=internal_current_bankroll,
            mismatch_detected=mismatch_detected,
            mismatch_details=mismatch_details,
        )

    except Exception as exc:
        logger.error("broker_reconciliation: sync failed — %s", exc, exc_info=True)
        return BrokerCapitalState(
            cash=0.0, buying_power=0.0, portfolio_value=0.0,
            available_capital=0.0, committed_capital=0.0,
            current_bankroll=internal_current_bankroll or 0.0,
            unrealized_pl=0.0, unrealized_pl_pct=0.0,
            reserved_by_open_orders=0.0, effective_buying_power=0.0,
            open_positions_count=0, open_buy_orders_count=0, open_sell_orders_count=0,
            last_sync_at=now_iso,
            broker_mode="paper" if ALPACA_PAPER else "live",
            sync_success=False,
            sync_error=str(exc),
            internal_available_capital=internal_available_capital,
            internal_committed_capital=internal_committed_capital,
            internal_current_bankroll=internal_current_bankroll,
        )


def cancel_stale_buy_orders() -> list[str]:
    """
    Cancel all unfilled buy orders older than STALE_ORDER_TIMEOUT_SECONDS.
    Returns list of cancelled order_ids. Never raises.
    """
    if not ALPACA_ENABLED:
        return []

    cancelled: list[str] = []
    try:
        adapter = get_alpaca_adapter()
        orders = adapter.list_orders(limit=50)
        open_statuses = {"new", "partially_filled", "accepted", "pending_new", "held"}
        for order in orders:
            if order.status not in open_statuses:
                continue
            if order.side not in ("buy", "BUY"):
                continue
            snap = _map_order_snapshot(order)
            if snap.is_stale:
                try:
                    success = adapter.cancel_order(order.order_id)
                    if success:
                        cancelled.append(order.order_id)
                        logger.info(
                            "cancel_stale_buy_orders: cancelled stale buy %s for %s "
                            "(age=%.0fs)",
                            order.order_id, order.symbol, snap.age_seconds,
                        )
                except Exception as exc:
                    logger.warning(
                        "cancel_stale_buy_orders: could not cancel %s — %s",
                        order.order_id, exc,
                    )
    except Exception as exc:
        logger.error("cancel_stale_buy_orders: adapter error — %s", exc)
    return cancelled


def get_live_open_orders() -> list:
    """Return all currently open orders from the broker. Never raises."""
    if not ALPACA_ENABLED:
        return []
    try:
        adapter = get_alpaca_adapter()
        orders = adapter.list_orders(limit=50)
        open_statuses = {"new", "partially_filled", "accepted", "pending_new", "held"}
        return [o for o in orders if o.status in open_statuses]
    except Exception as exc:
        logger.error("get_live_open_orders: %s", exc)
        return []


def is_symbol_held(symbol: str) -> bool:
    """Return True if the broker currently holds a non-zero position for symbol."""
    if not ALPACA_ENABLED:
        return False
    try:
        positions = get_alpaca_adapter().get_positions()
        return any(p.symbol.upper() == symbol.upper() for p in positions)
    except Exception as exc:
        logger.warning("is_symbol_held(%s): %s", symbol, exc)
        return False


def is_symbol_order_pending(symbol: str) -> bool:
    """Return True if the broker has an open order for symbol (buy or sell)."""
    orders = get_live_open_orders()
    return any(
        getattr(o, "symbol", "").upper() == symbol.upper() for o in orders
    )


def has_sufficient_buying_power(notional: float) -> bool:
    """
    Return True if effective buying power covers `notional` plus the reserve
    buffer. This is a quick eligibility gate before placing a buy.
    """
    state = get_broker_capital_state()
    if not state.sync_success:
        return False
    required = notional + MIN_REQUIRED_BUYING_POWER
    return state.effective_buying_power >= required


def broker_capital_state_to_dict(state: BrokerCapitalState) -> dict:
    """Serialise BrokerCapitalState to a JSON-safe dict for API responses."""
    return {
        "cash": state.cash,
        "buying_power": state.buying_power,
        "portfolio_value": state.portfolio_value,
        "available_capital": state.available_capital,
        "committed_capital": state.committed_capital,
        "current_bankroll": state.current_bankroll,
        "unrealized_pl": state.unrealized_pl,
        "unrealized_pl_pct": state.unrealized_pl_pct,
        "reserved_by_open_orders": state.reserved_by_open_orders,
        "effective_buying_power": state.effective_buying_power,
        "open_positions_count": state.open_positions_count,
        "open_buy_orders_count": state.open_buy_orders_count,
        "open_sell_orders_count": state.open_sell_orders_count,
        "funded_packets": state.open_positions_count,  # alias for dashboard card
        "last_sync_at": state.last_sync_at,
        "broker_mode": state.broker_mode,
        "sync_success": state.sync_success,
        "sync_error": state.sync_error,
        "internal_available_capital": state.internal_available_capital,
        "internal_committed_capital": state.internal_committed_capital,
        "internal_current_bankroll": state.internal_current_bankroll,
        "mismatch_detected": state.mismatch_detected,
        "mismatch_details": state.mismatch_details,
        "positions": [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "market_value": p.market_value,
                "avg_entry_price": p.avg_entry_price,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_pl_pct": p.unrealized_pl_pct,
                "side": p.side,
                "hold_minutes": p.hold_minutes,
            }
            for p in state.positions
        ],
        "open_buy_orders": [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "qty": o.qty,
                "notional": o.notional,
                "status": o.status,
                "age_seconds": o.age_seconds,
                "is_stale": o.is_stale,
            }
            for o in state.open_buy_orders
        ],
        "open_sell_orders": [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "qty": o.qty,
                "notional": o.notional,
                "status": o.status,
                "age_seconds": o.age_seconds,
            }
            for o in state.open_sell_orders
        ],
    }
