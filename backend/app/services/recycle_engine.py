"""
INTRADAY_RECYCLE execution engine.

This module implements Hunter's live capital-recycling strategy. Every cycle:

  1. Sync broker state (account, positions, open orders)
  2. Cancel stale unfilled buy orders
  3. Evaluate open positions for exit (profit target, stop loss, max hold, EOD)
  4. Execute pending exits — sell first
  5. Refresh broker state after exits
  6. Evaluate replacement opportunities (weakest position vs new candidates)
  7. Evaluate new entries only after capital is confirmed available
  8. Place buy orders within exposure limits

Position lifecycle states
-------------------------
candidate       — symbol passed screening, not yet ranked
ranked          — scored and eligible for entry
queued          — selected for next buy cycle
buy_submitted   — buy order placed, awaiting fill
partially_filled — buy partially filled
filled          — fully filled, now in monitoring
monitoring      — active position, exit criteria evaluated each cycle
exit_ready      — exit criterion triggered, sell order pending
sell_submitted  — sell order placed, awaiting fill
partially_closed — sell partially filled
closed          — fully sold, capital returned
canceled        — buy or sell order cancelled
failed          — order rejected or unrecoverable error

Public API
----------
run_recycle_cycle()     → RecycleCycleResult
get_cycle_status()      → dict
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.config import (
    ALLOW_OVERNIGHT_HOLD,
    ALPACA_ENABLED,
    ALPACA_PAPER,
    CAPITAL_RESERVE_BUFFER,
    EXECUTION_MODE,
    FAST_RECYCLE_MAX_HOLD_MINUTES,
    FAST_RECYCLE_MAX_OPEN_POSITIONS,
    FAST_RECYCLE_MAX_POSITION_DOLLARS,
    FAST_RECYCLE_TRANCHE,
    FORCE_SELL_END_OF_DAY,
    LIVE_EXECUTION_PROFILE,
    LIVE_EXECUTION_STRATEGY,
    MAX_HOLD_MINUTES,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_DOLLARS,
    MAX_POSITION_PCT_OF_BANKROLL,
    MIN_REQUIRED_BUYING_POWER,
    PYRAMIDING_ENABLED,
    RECYCLE_EOD_FLATTEN_MINUTES_BEFORE,
    REPLACEMENT_RANK_THRESHOLD,
    STALE_ORDER_TIMEOUT_SECONDS,
    STOP_LOSS_PCT,
    STRATEGY_MODE,
    TARGET_PROFIT_PCT,
    USE_ONLY_FAST_RECYCLE_BUCKET,
)
from app.services.broker_reconciliation import (
    BrokerCapitalState,
    broker_capital_state_to_dict,
    cancel_stale_buy_orders,
    get_broker_capital_state,
    is_symbol_held,
    is_symbol_order_pending,
)
from app.integration.brokerage.alpaca import get_alpaca_adapter

logger = logging.getLogger(__name__)

# ── Position lifecycle states ─────────────────────────────────────────────────

class PositionState:
    CANDIDATE = "candidate"
    RANKED = "ranked"
    QUEUED = "queued"
    BUY_SUBMITTED = "buy_submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    MONITORING = "monitoring"
    EXIT_READY = "exit_ready"
    SELL_SUBMITTED = "sell_submitted"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    CANCELED = "canceled"
    FAILED = "failed"


# ── Exit reasons ──────────────────────────────────────────────────────────────

class ExitReason:
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    MAX_HOLD_TIME = "max_hold_time"
    REPLACEMENT = "replacement"
    END_OF_DAY = "end_of_day"
    MANUAL = "manual"
    OVERNIGHT_PREVENTION = "overnight_prevention"


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ExitDecision:
    symbol: str
    exit_reason: str
    unrealized_pl_pct: float
    hold_minutes: Optional[float]
    market_value: float
    capital_bucket: str = "legacy"
    max_hold_minutes: Optional[float] = None
    stale_position: bool = False


@dataclass
class EntryDecision:
    symbol: str
    notional: float
    confidence: float
    score: float
    source_id: Optional[str]
    capital_bucket: str = "legacy"
    execution_profile: Optional[str] = None
    max_hold_minutes: Optional[float] = None
    entry_guard: str = "no_duplicate_symbol_and_sufficient_buying_power"
    stop_loss_pct: float = STOP_LOSS_PCT
    profit_target_pct: float = TARGET_PROFIT_PCT


@dataclass
class RecycleCycleResult:
    cycle_at: str                               # ISO timestamp

    # Broker state at start of cycle
    broker_state: Optional[dict] = None

    # Stale order cleanup
    stale_orders_cancelled: list[str] = field(default_factory=list)

    # Exit decisions and their outcomes
    exit_decisions: list[dict] = field(default_factory=list)
    exits_submitted: int = 0
    exits_failed: int = 0

    # Entry decisions and their outcomes
    entry_decisions: list[dict] = field(default_factory=list)
    entries_submitted: int = 0
    entries_failed: int = 0
    entries_skipped: int = 0

    # Replacement activity
    replacements_triggered: int = 0

    # Post-cycle broker state (after exits and entries)
    broker_state_post: Optional[dict] = None

    # Errors and warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Cycle metadata
    strategy_mode: str = STRATEGY_MODE
    live_execution_strategy: str = LIVE_EXECUTION_STRATEGY
    is_live_mode: bool = EXECUTION_MODE == "live"
    cycle_skipped: bool = False
    skip_reason: Optional[str] = None
    fast_recycle: Optional[dict] = None


@dataclass
class FastRecycleState:
    total_capital: float
    gross_capital: float
    available_to_deploy: float
    deployed_capital: float
    open_positions_count: int
    legacy_open_positions_count: int
    enabled: bool


# ── EOD detection ─────────────────────────────────────────────────────────────

def _is_near_market_close() -> bool:
    """Return True if we are within RECYCLE_EOD_FLATTEN_MINUTES_BEFORE of 4 PM ET."""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        try:
            from dateutil import tz as dateutil_tz
            et = dateutil_tz.gettz("America/New_York")
        except Exception:
            return False

    now_et = datetime.now(et)
    # Only check on weekdays during regular hours
    if now_et.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    market_close_hour = 16      # 4 PM ET
    minutes_to_close = (market_close_hour * 60) - (now_et.hour * 60 + now_et.minute)
    return 0 <= minutes_to_close <= RECYCLE_EOD_FLATTEN_MINUTES_BEFORE


def _is_outside_market_hours() -> bool:
    """Return True if current time is outside regular US market hours (9:30–16:00 ET)."""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        try:
            from dateutil import tz as dateutil_tz
            et = dateutil_tz.gettz("America/New_York")
        except Exception:
            return False

    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        return True
    open_minutes = 9 * 60 + 30
    close_minutes = 16 * 60
    current_minutes = now_et.hour * 60 + now_et.minute
    return current_minutes < open_minutes or current_minutes >= close_minutes


# ── Exit evaluation ───────────────────────────────────────────────────────────

def evaluate_exits(
    broker_state: BrokerCapitalState,
    *,
    position_meta: Optional[dict[str, dict]] = None,
) -> list[ExitDecision]:
    """
    Examine all open positions and return a list of ExitDecisions.
    Every RECYCLE-mode position must have a defined exit pathway.
    """
    if not broker_state.sync_success:
        return []

    decisions: list[ExitDecision] = []
    near_close = _is_near_market_close()

    for pos in broker_state.positions:
        reason: Optional[str] = None
        upl_pct = pos.unrealized_pl_pct
        hold_min = pos.hold_minutes
        meta = position_meta.get(pos.symbol.upper(), {}) if position_meta else {}
        capital_bucket = meta.get("capital_bucket", "legacy")
        max_hold_limit = float(
            meta.get("max_hold_minutes")
            or (FAST_RECYCLE_MAX_HOLD_MINUTES if capital_bucket == "fast_recycle" else MAX_HOLD_MINUTES)
        )

        # 1. Profit target
        if upl_pct >= TARGET_PROFIT_PCT:
            reason = ExitReason.PROFIT_TARGET

        # 2. Stop loss
        elif upl_pct <= STOP_LOSS_PCT:
            reason = ExitReason.STOP_LOSS

        # 3. Max hold time
        elif hold_min is not None and hold_min >= max_hold_limit:
            reason = ExitReason.MAX_HOLD_TIME

        # 4. EOD flattening (RECYCLE mode never holds overnight unless explicitly allowed)
        elif near_close and FORCE_SELL_END_OF_DAY and not ALLOW_OVERNIGHT_HOLD:
            reason = ExitReason.END_OF_DAY

        # 5. Overnight prevention — outside hours and overnight hold is off
        elif _is_outside_market_hours() and not ALLOW_OVERNIGHT_HOLD:
            reason = ExitReason.OVERNIGHT_PREVENTION

        if reason:
            decisions.append(ExitDecision(
                symbol=pos.symbol,
                exit_reason=reason,
                unrealized_pl_pct=round(upl_pct * 100, 3),
                hold_minutes=hold_min,
                market_value=pos.market_value,
                capital_bucket=capital_bucket,
                max_hold_minutes=max_hold_limit,
                stale_position=reason == ExitReason.MAX_HOLD_TIME,
            ))
            logger.info(
                "recycle_engine: exit_ready %s reason=%s upl=%.3f%% hold=%.0fm",
                pos.symbol, reason, upl_pct * 100,
                hold_min or 0,
            )

    return decisions


def evaluate_replacements(
    broker_state: BrokerCapitalState,
    candidates: list[dict],
) -> list[ExitDecision]:
    """
    When capital is constrained and a stronger candidate exists, mark the
    weakest current position as exit_ready for replacement.

    A replacement is triggered when:
    - All position slots are full (open_positions_count >= MAX_OPEN_POSITIONS)
    - A candidate's score is >= REPLACEMENT_RANK_THRESHOLD better than the
      weakest held position's unrealized_pl_pct (as a proxy for quality)
    - The candidate passes all entry eligibility checks
    """
    if not broker_state.sync_success:
        return []
    if not candidates:
        return []
    if broker_state.open_positions_count < MAX_OPEN_POSITIONS:
        return []   # There is room — no replacement needed

    if not broker_state.positions:
        return []

    # Find weakest held position by unrealized P/L pct
    weakest = min(broker_state.positions, key=lambda p: p.unrealized_pl_pct)

    # Find best new candidate (highest confidence)
    best_candidate = max(candidates, key=lambda c: c.get("confidence", 0.0))
    best_conf = float(best_candidate.get("confidence", 0.0))

    # Replacement is only worth it if new candidate is materially better
    # than the weakest position's current return
    improvement = best_conf - max(0.0, weakest.unrealized_pl_pct + 0.5)
    if improvement < REPLACEMENT_RANK_THRESHOLD:
        return []

    logger.info(
        "recycle_engine: replacement triggered — weakest=%s upl=%.3f%% "
        "candidate=%s conf=%.3f improvement=%.3f",
        weakest.symbol, weakest.unrealized_pl_pct * 100,
        best_candidate.get("symbol", "?"), best_conf, improvement,
    )
    return [ExitDecision(
        symbol=weakest.symbol,
        exit_reason=ExitReason.REPLACEMENT,
        unrealized_pl_pct=round(weakest.unrealized_pl_pct * 100, 3),
        hold_minutes=weakest.hold_minutes,
        market_value=weakest.market_value,
    )]


def execute_exits(exit_decisions: list[ExitDecision]) -> tuple[list[dict], list[dict]]:
    """
    Place sell orders for all exit decisions. Returns (submitted, failed) lists.
    Each list contains dicts with order details.
    Never raises.
    """
    submitted: list[dict] = []
    failed: list[dict] = []

    if not ALPACA_ENABLED:
        logger.debug("execute_exits: ALPACA_ENABLED=False — skipping")
        return submitted, failed

    try:
        from app.integration.brokerage.base import TradeOrder
        adapter = get_alpaca_adapter()
    except Exception as exc:
        logger.error("execute_exits: adapter unavailable — %s", exc)
        for d in exit_decisions:
            failed.append({"symbol": d.symbol, "reason": d.exit_reason, "error": str(exc)})
        return submitted, failed

    for decision in exit_decisions:
        try:
            order = TradeOrder(
                symbol=decision.symbol,
                side="sell",
                order_type="market",
                qty=None,       # qty=None with notional=None means close entire position
                notional=None,
                time_in_force="day",
            )
            # Use fractional qty from position
            _state = get_broker_capital_state()
            pos = next((p for p in _state.positions if p.symbol == decision.symbol), None)
            if pos and pos.qty > 0:
                order.qty = pos.qty

            result = adapter.place_order(order)
            try:
                from sqlmodel import Session
                from app.database.config import engine
                from app.services import position_lifecycle as lifecycle_svc

                with Session(engine) as session:
                    lifecycle_svc.record_exit_submission(
                        session,
                        symbol=decision.symbol,
                        provider_order_id=result.order_id,
                        submitted_at=datetime.now(timezone.utc),
                        stale_reason="max_hold_exceeded" if decision.stale_position else None,
                        exit_reason=decision.exit_reason,
                    )
            except Exception as lifecycle_exc:
                logger.warning(
                    "execute_exits: could not persist timing lifecycle for %s — %s",
                    decision.symbol,
                    lifecycle_exc,
                )
            submitted.append({
                "symbol": decision.symbol,
                "exit_reason": decision.exit_reason,
                "order_id": result.order_id,
                "status": result.status,
                "qty": result.qty,
                "unrealized_pl_pct": decision.unrealized_pl_pct,
            })
            logger.info(
                "execute_exits: SELL submitted %s qty=%.4f reason=%s order_id=%s status=%s",
                decision.symbol, order.qty or 0, decision.exit_reason,
                result.order_id, result.status,
            )
        except Exception as exc:
            logger.error("execute_exits: SELL failed for %s — %s", decision.symbol, exc)
            failed.append({
                "symbol": decision.symbol,
                "exit_reason": decision.exit_reason,
                "error": str(exc),
            })

    return submitted, failed


# ── Entry evaluation ──────────────────────────────────────────────────────────

def evaluate_entries(
    broker_state: BrokerCapitalState,
    candidates: list[dict],
    *,
    fast_recycle: Optional[FastRecycleState] = None,
) -> list[EntryDecision]:
    """
    Select eligible entry candidates given current broker state.

    All of the following must be true for a symbol to be entered:
    - score >= 0 (any ranked candidate)
    - symbol not already held
    - symbol does not have an open order pending
    - open_positions_count < MAX_OPEN_POSITIONS
    - projected notional <= MAX_POSITION_DOLLARS
    - projected notional <= MAX_POSITION_PCT_OF_BANKROLL * current_bankroll
    - effective_buying_power >= projected_notional + MIN_REQUIRED_BUYING_POWER
    - not PYRAMIDING when symbol already held
    """
    if not broker_state.sync_success:
        return []
    if not candidates:
        return []

    fast_mode = bool(
        fast_recycle
        and fast_recycle.enabled
        and LIVE_EXECUTION_PROFILE == "FAST_RECYCLE"
        and USE_ONLY_FAST_RECYCLE_BUCKET
    )
    current_bankroll = fast_recycle.gross_capital if fast_mode else broker_state.current_bankroll
    effective_bp = fast_recycle.available_to_deploy if fast_mode else broker_state.effective_buying_power
    open_count = fast_recycle.open_positions_count if fast_mode else broker_state.open_positions_count
    max_positions_limit = FAST_RECYCLE_MAX_OPEN_POSITIONS if fast_mode else MAX_OPEN_POSITIONS
    max_position_dollars = FAST_RECYCLE_MAX_POSITION_DOLLARS if fast_mode else MAX_POSITION_DOLLARS
    max_hold_minutes = FAST_RECYCLE_MAX_HOLD_MINUTES if fast_mode else MAX_HOLD_MINUTES
    held_symbols = {p.symbol.upper() for p in broker_state.positions}
    pending_symbols = {
        o.symbol.upper()
        for o in [*broker_state.open_buy_orders, *broker_state.open_sell_orders]
    }

    entries: list[EntryDecision] = []
    budget_remaining = effective_bp

    sorted_candidates = sorted(
        candidates,
        key=lambda x: (
            _candidate_expected_hold_minutes(x, max_hold_minutes) if fast_mode else 0,
            -float(x.get("confidence", 0.0)),
            -float(x.get("estimated_profit", 0.0)),
        ),
    )

    for c in sorted_candidates:
        if open_count + len(entries) >= max_positions_limit:
            logger.debug(
                "evaluate_entries: MAX_OPEN_POSITIONS=%d reached — no more entries",
                max_positions_limit,
            )
            break

        symbol = (c.get("symbol") or _extract_symbol_from_candidate(c) or "").upper()
        if not symbol:
            continue

        # Already held?
        if symbol in held_symbols and not PYRAMIDING_ENABLED:
            logger.debug("evaluate_entries: %s already held — skip", symbol)
            continue

        # Already has an open order?
        if symbol in pending_symbols:
            logger.debug("evaluate_entries: %s has pending order — skip", symbol)
            continue

        confidence = float(c.get("confidence", 0.0))
        estimated_profit = float(c.get("estimated_profit", 0.0))

        # Size the position
        notional = _compute_notional(
            estimated_profit,
            current_bankroll,
            budget_remaining,
            max_position_dollars=max_position_dollars,
        )
        if notional <= 0:
            logger.debug("evaluate_entries: %s notional=$0 — skip", symbol)
            continue

        # Buying power check
        required_bp = notional + MIN_REQUIRED_BUYING_POWER
        if budget_remaining < required_bp:
            logger.debug(
                "evaluate_entries: %s insufficient bp — need=%.2f have=%.2f",
                symbol, required_bp, budget_remaining,
            )
            continue

        entries.append(EntryDecision(
            symbol=symbol,
            notional=notional,
            confidence=confidence,
            score=float(c.get("score", 0.0)),
            source_id=c.get("source_id"),
            capital_bucket="fast_recycle" if fast_mode else "legacy",
            execution_profile=LIVE_EXECUTION_PROFILE if fast_mode else LIVE_EXECUTION_STRATEGY,
            max_hold_minutes=max_hold_minutes,
        ))
        budget_remaining -= notional
        logger.info(
            "evaluate_entries: ENTRY approved %s notional=%.2f conf=%.3f",
            symbol, notional, confidence,
        )

    return entries


def _extract_symbol_from_candidate(c: dict) -> Optional[str]:
    """Extract ticker from candidate dict (notes field or description)."""
    import re
    for field_name in ("notes", "description"):
        text = c.get(field_name) or ""
        m = re.search(r"\bsymbol\s*[:=]\s*([A-Z]{1,5})\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        m = re.search(r"\$([A-Z]{1,5})\b", text)
        if m:
            return m.group(1).upper()
    return None


def _compute_notional(
    estimated_profit: float,
    current_bankroll: float,
    budget_remaining: float,
    *,
    max_position_dollars: float = MAX_POSITION_DOLLARS,
) -> float:
    """
    Compute a safe position size that fits within the strategy caps.
    Returns 0.0 if no safe size exists.
    """
    if current_bankroll <= 0:
        return 0.0

    bankroll_cap = round(current_bankroll * MAX_POSITION_PCT_OF_BANKROLL, 2)
    dollar_cap = max_position_dollars
    budget_cap = round(max(0.0, budget_remaining - MIN_REQUIRED_BUYING_POWER - CAPITAL_RESERVE_BUFFER), 2)

    notional = round(min(dollar_cap, bankroll_cap, budget_cap), 2)
    return max(0.0, notional)


def execute_entries(entry_decisions: list[EntryDecision]) -> tuple[list[dict], list[dict]]:
    """
    Place buy orders for all approved entry decisions.
    Returns (submitted, failed) lists.
    Never raises.
    """
    submitted: list[dict] = []
    failed: list[dict] = []

    if not ALPACA_ENABLED:
        return submitted, failed

    try:
        from app.integration.brokerage.base import TradeOrder
        adapter = get_alpaca_adapter()
    except Exception as exc:
        logger.error("execute_entries: adapter unavailable — %s", exc)
        for e in entry_decisions:
            failed.append({"symbol": e.symbol, "error": str(exc)})
        return submitted, failed

    for entry in entry_decisions:
        # Final check: re-verify the symbol is not already held (broker state
        # may have changed between evaluate_entries and execute_entries)
        if is_symbol_held(entry.symbol) and not PYRAMIDING_ENABLED:
            logger.warning(
                "execute_entries: %s now held — skipping buy to prevent duplicate",
                entry.symbol,
            )
            failed.append({"symbol": entry.symbol, "error": "already_held_at_execution_time"})
            continue

        if is_symbol_order_pending(entry.symbol):
            logger.warning(
                "execute_entries: %s has pending order at execution — skip",
                entry.symbol,
            )
            failed.append({"symbol": entry.symbol, "error": "pending_order_at_execution_time"})
            continue

        try:
            order = TradeOrder(
                symbol=entry.symbol,
                side="buy",
                order_type="market",
                notional=entry.notional,
                time_in_force="day",
            )
            result = adapter.place_order(order)
            try:
                from sqlmodel import Session
                from app.database.config import engine
                from app.services import position_lifecycle as lifecycle_svc

                with Session(engine) as session:
                    lifecycle_svc.record_entry_submission(
                        session,
                        symbol=entry.symbol,
                        source_id=entry.source_id,
                        provider_order_id=result.order_id,
                        entered_at=datetime.now(timezone.utc),
                        max_hold_minutes=entry.max_hold_minutes,
                        capital_bucket=entry.capital_bucket,
                        execution_profile=entry.execution_profile,
                    )
            except Exception as lifecycle_exc:
                logger.warning(
                    "execute_entries: could not persist timing lifecycle for %s — %s",
                    entry.symbol,
                    lifecycle_exc,
                )
            submitted.append({
                "symbol": entry.symbol,
                "notional": entry.notional,
                "order_id": result.order_id,
                "status": result.status,
                "confidence": entry.confidence,
                "source_id": entry.source_id,
            })
            logger.info(
                "execute_entries: BUY submitted %s notional=%.2f order_id=%s status=%s mode=%s",
                entry.symbol, entry.notional, result.order_id, result.status,
                "paper" if ALPACA_PAPER else "live",
            )
        except Exception as exc:
            err_str = str(exc)
            logger.error("execute_entries: BUY failed for %s — %s", entry.symbol, err_str)
            failed.append({
                "symbol": entry.symbol,
                "notional": entry.notional,
                "error": err_str,
            })

    return submitted, failed


# ── Candidate source ──────────────────────────────────────────────────────────

def _load_live_candidates() -> list[dict]:
    """
    Load currently ranked trading candidates from the live database.
    Returns top MAX_OPEN_POSITIONS*2 candidates by score.
    """
    try:
        from app.database.config import engine
        from app.models.income_source import IncomeSource, SourceStatus
        from app.models.decision import ExecutionPath, OpportunityDecision
        from sqlmodel import Session, select

        with Session(engine) as session:
            rows = session.exec(
                select(IncomeSource, OpportunityDecision)
                .join(
                    OpportunityDecision,
                    OpportunityDecision.source_id == IncomeSource.source_id,
                    isouter=True,
                )
                .where(
                    IncomeSource.status.in_([
                        SourceStatus.new, SourceStatus.scored,
                        SourceStatus.review_ready, SourceStatus.budgeted,
                    ]),
                    IncomeSource.score.isnot(None),
                )
                .order_by(IncomeSource.score.desc())
                .limit(MAX_OPEN_POSITIONS * 3)
            ).all()

            candidates = []
            for source, decision in rows:
                if decision and decision.execution_path != ExecutionPath.trading:
                    continue
                candidates.append({
                    "source_id": source.source_id,
                    "description": source.description or "",
                    "notes": source.notes or "",
                    "score": float(source.score or 0),
                    "confidence": float(source.confidence or 0),
                    "estimated_profit": float(source.estimated_profit or 0),
                    "symbol": _extract_symbol_from_candidate({
                        "notes": source.notes or "",
                        "description": source.description or "",
                    }),
                    "category": source.category,
                })
            return [c for c in candidates if c.get("symbol")]
    except Exception as exc:
        logger.error("_load_live_candidates: %s", exc)
        return []


def _candidate_expected_hold_minutes(candidate: dict, default_hold_minutes: int) -> float:
    raw = candidate.get("expected_hold_minutes") or candidate.get("max_hold_minutes")
    try:
        value = float(raw)
        return value if value > 0 else float(default_hold_minutes)
    except (TypeError, ValueError):
        return float(default_hold_minutes)


def _get_position_bucket_map() -> dict[str, dict]:
    from sqlmodel import Session
    from app.database.config import engine
    from app.services import position_lifecycle as lifecycle_svc

    with Session(engine) as session:
        open_fast = lifecycle_svc.get_lifecycles_by_bucket(
            session,
            capital_bucket="fast_recycle",
            status="open",
        )
        open_legacy = lifecycle_svc.get_lifecycles_by_bucket(
            session,
            capital_bucket="legacy",
            status="open",
        )
        open_lifecycles = [*open_fast, *open_legacy]
        return {
            lifecycle.symbol.upper(): (lifecycle_svc.serialize_lifecycle(lifecycle) or {})
            for lifecycle in open_lifecycles
        }


def _get_fast_recycle_state(broker_state: BrokerCapitalState) -> FastRecycleState:
    if LIVE_EXECUTION_PROFILE != "FAST_RECYCLE" or not USE_ONLY_FAST_RECYCLE_BUCKET:
        return FastRecycleState(
            total_capital=FAST_RECYCLE_TRANCHE,
            gross_capital=FAST_RECYCLE_TRANCHE,
            available_to_deploy=0.0,
            deployed_capital=0.0,
            open_positions_count=0,
            legacy_open_positions_count=broker_state.open_positions_count,
            enabled=False,
        )

    from sqlmodel import Session
    from app.database.config import engine
    from app.services import position_lifecycle as lifecycle_svc

    with Session(engine) as session:
        open_fast = lifecycle_svc.get_lifecycles_by_bucket(
            session,
            capital_bucket="fast_recycle",
            status="open",
        )
        closed_fast = lifecycle_svc.get_lifecycles_by_bucket(
            session,
            capital_bucket="fast_recycle",
            status="closed",
        )

    fast_symbols = {lifecycle.symbol.upper() for lifecycle in open_fast}
    fast_order_ids = {lifecycle.entry_order_id for lifecycle in open_fast if lifecycle.entry_order_id}
    fast_positions = [pos for pos in broker_state.positions if pos.symbol.upper() in fast_symbols]
    fast_open_orders = [
        order for order in broker_state.open_buy_orders
        if order.order_id in fast_order_ids or order.symbol.upper() in fast_symbols
    ]

    realized_fast = round(
        sum(float(lifecycle.realized_pl or 0.0) for lifecycle in closed_fast),
        2,
    )
    gross_capital = round(max(0.0, FAST_RECYCLE_TRANCHE + realized_fast), 2)
    deployed_capital = round(
        sum(float(pos.market_value or 0.0) for pos in fast_positions)
        + sum(float(order.notional or 0.0) for order in fast_open_orders),
        2,
    )
    internal_available = round(
        max(0.0, gross_capital - deployed_capital - CAPITAL_RESERVE_BUFFER),
        2,
    )
    available_to_deploy = round(
        max(0.0, min(internal_available, broker_state.effective_buying_power)),
        2,
    )
    return FastRecycleState(
        total_capital=FAST_RECYCLE_TRANCHE,
        gross_capital=gross_capital,
        available_to_deploy=available_to_deploy,
        deployed_capital=deployed_capital,
        open_positions_count=len(fast_positions),
        legacy_open_positions_count=max(0, broker_state.open_positions_count - len(fast_positions)),
        enabled=True,
    )


def _fast_recycle_state_to_dict(state: FastRecycleState) -> dict:
    return {
        "enabled": state.enabled,
        "total_capital": round(state.total_capital, 2),
        "gross_capital": round(state.gross_capital, 2),
        "available_to_deploy": round(state.available_to_deploy, 2),
        "deployed_capital": round(state.deployed_capital, 2),
        "open_positions_count": state.open_positions_count,
        "legacy_open_positions_count": state.legacy_open_positions_count,
    }


# ── Main cycle ────────────────────────────────────────────────────────────────

def run_recycle_cycle() -> RecycleCycleResult:
    """
    Execute one full INTRADAY_RECYCLE cycle. This is the primary loop:

      1. Validate mode and market hours
      2. Cancel stale buy orders
      3. Sync broker state
      4. Evaluate exits → execute exits
      5. Refresh broker state
      6. Evaluate replacements
      7. Load candidates
      8. Evaluate entries → execute entries
      9. Final broker sync
     10. Return cycle result
    """

    result = RecycleCycleResult(cycle_at=datetime.now(timezone.utc).isoformat())

    # ── Guard: RECYCLE mode only ──────────────────────────────────────────────
    if STRATEGY_MODE != "RECYCLE":
        result.cycle_skipped = True
        result.skip_reason = f"STRATEGY_MODE={STRATEGY_MODE} — only RECYCLE mode runs this cycle"
        logger.debug("run_recycle_cycle: skipped (STRATEGY_MODE=%s)", STRATEGY_MODE)
        return result

    # ── Guard: Outside market hours and overnight hold disabled ───────────────
    if _is_outside_market_hours() and not ALLOW_OVERNIGHT_HOLD:
        # Still try to exit any stale positions held overnight
        logger.info("run_recycle_cycle: outside market hours — running exit-only sweep")
        stale_cancelled = cancel_stale_buy_orders()
        result.stale_orders_cancelled = stale_cancelled

        broker_state = get_broker_capital_state()
        result.broker_state = broker_capital_state_to_dict(broker_state)
        result.fast_recycle = _fast_recycle_state_to_dict(_get_fast_recycle_state(broker_state))

        if broker_state.open_positions_count > 0:
            exit_decisions = evaluate_exits(
                broker_state,
                position_meta=_get_position_bucket_map(),
            )
            if exit_decisions:
                exits_ok, exits_fail = execute_exits(exit_decisions)
                result.exit_decisions = [vars(d) for d in exit_decisions]
                result.exits_submitted = len(exits_ok)
                result.exits_failed = len(exits_fail)
        return result

    # ── Step 1: Cancel stale buy orders ──────────────────────────────────────
    stale_cancelled = cancel_stale_buy_orders()
    result.stale_orders_cancelled = stale_cancelled
    if stale_cancelled:
        logger.info("run_recycle_cycle: cancelled %d stale buy orders", len(stale_cancelled))

    # ── Step 2: Sync broker state ─────────────────────────────────────────────
    broker_state = get_broker_capital_state()
    result.broker_state = broker_capital_state_to_dict(broker_state)
    fast_recycle = _get_fast_recycle_state(broker_state)
    result.fast_recycle = _fast_recycle_state_to_dict(fast_recycle)

    if not broker_state.sync_success:
        result.errors.append(f"broker_sync_failed: {broker_state.sync_error}")
        logger.error("run_recycle_cycle: broker sync failed — %s", broker_state.sync_error)
        return result

    logger.info(
        "run_recycle_cycle: broker_sync — positions=%d buy_orders=%d "
        "buying_power=%.2f effective_bp=%.2f",
        broker_state.open_positions_count,
        broker_state.open_buy_orders_count,
        broker_state.buying_power,
        broker_state.effective_buying_power,
    )

    # ── Step 3: Evaluate and execute exits ───────────────────────────────────
    exit_decisions = evaluate_exits(
        broker_state,
        position_meta=_get_position_bucket_map(),
    )

    if exit_decisions:
        exits_ok, exits_fail = execute_exits(exit_decisions)
        result.exit_decisions = [vars(d) for d in exit_decisions]
        result.exits_submitted = len(exits_ok)
        result.exits_failed = len(exits_fail)

        if exits_ok:
            logger.info(
                "run_recycle_cycle: %d sell orders submitted, waiting for fills",
                len(exits_ok),
            )
            # Give fills a moment to process before re-syncing
            import time as _time
            _time.sleep(2)

        # ── Step 4: Refresh broker state after exits ──────────────────────────
        broker_state = get_broker_capital_state()
        if not broker_state.sync_success:
            result.errors.append("post_exit_sync_failed")
            return result

    # ── Step 5: Load candidates from database ─────────────────────────────────
    candidates = _load_live_candidates()

    # ── Step 6: Evaluate replacements ─────────────────────────────────────────
    if candidates and broker_state.open_positions_count >= MAX_OPEN_POSITIONS:
        replacement_exits = evaluate_replacements(broker_state, candidates)
        if replacement_exits:
            result.replacements_triggered = len(replacement_exits)
            rep_ok, rep_fail = execute_exits(replacement_exits)
            result.exits_submitted += len(rep_ok)
            result.exits_failed += len(rep_fail)
            if rep_ok:
                import time as _time
                _time.sleep(2)
                broker_state = get_broker_capital_state()
                if not broker_state.sync_success:
                    result.errors.append("post_replacement_sync_failed")
                    return result

    # ── Step 7: Evaluate and execute entries ──────────────────────────────────
    if broker_state.effective_buying_power < MIN_REQUIRED_BUYING_POWER:
        result.entries_skipped += len(candidates)
        result.warnings.append(
            f"Insufficient buying power ({broker_state.effective_buying_power:.2f}) "
            f"— need {MIN_REQUIRED_BUYING_POWER:.2f} minimum. No new buys placed."
        )
        logger.info(
            "run_recycle_cycle: insufficient buying_power=%.2f — skipping entries",
            broker_state.effective_buying_power,
        )
    else:
        entry_decisions = evaluate_entries(
            broker_state,
            candidates,
            fast_recycle=fast_recycle,
        )
        if entry_decisions:
            entries_ok, entries_fail = execute_entries(entry_decisions)
            result.entry_decisions = [vars(e) for e in entry_decisions]
            result.entries_submitted = len(entries_ok)
            result.entries_failed = len(entries_fail)
            result.entries_skipped = max(0, len(candidates) - len(entry_decisions))
        else:
            result.entries_skipped = len(candidates)

    # ── Step 8: Final broker sync ─────────────────────────────────────────────
    final_state = get_broker_capital_state()
    result.broker_state_post = broker_capital_state_to_dict(final_state)
    result.fast_recycle = _fast_recycle_state_to_dict(_get_fast_recycle_state(final_state))

    logger.info(
        "run_recycle_cycle: complete — exits=%d entries=%d stale_cancelled=%d "
        "replacements=%d positions_now=%d buying_power_now=%.2f",
        result.exits_submitted,
        result.entries_submitted,
        len(result.stale_orders_cancelled),
        result.replacements_triggered,
        final_state.open_positions_count,
        final_state.buying_power,
    )
    return result


# ── Status helper ─────────────────────────────────────────────────────────────

def get_cycle_status() -> dict:
    """
    Return the current recycle engine configuration and live broker state.
    Used by the /budget/capital-state and /system/status endpoints.
    """
    broker_state = get_broker_capital_state()
    fast_recycle = _get_fast_recycle_state(broker_state)
    return {
        "strategy_mode": STRATEGY_MODE,
        "live_execution_strategy": LIVE_EXECUTION_STRATEGY,
        "live_execution_profile": LIVE_EXECUTION_PROFILE,
        "execution_mode": EXECUTION_MODE,
        "is_live": EXECUTION_MODE == "live",
        "is_paper": ALPACA_PAPER,
        "config": {
            "max_open_positions": MAX_OPEN_POSITIONS,
            "max_position_dollars": MAX_POSITION_DOLLARS,
            "max_position_pct_of_bankroll": MAX_POSITION_PCT_OF_BANKROLL,
            "min_required_buying_power": MIN_REQUIRED_BUYING_POWER,
            "capital_reserve_buffer": CAPITAL_RESERVE_BUFFER,
            "target_profit_pct": TARGET_PROFIT_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "stale_order_timeout_seconds": STALE_ORDER_TIMEOUT_SECONDS,
            "replacement_rank_threshold": REPLACEMENT_RANK_THRESHOLD,
            "force_sell_end_of_day": FORCE_SELL_END_OF_DAY,
            "allow_overnight_hold": ALLOW_OVERNIGHT_HOLD,
            "pyramiding_enabled": PYRAMIDING_ENABLED,
            "fast_recycle_tranche": FAST_RECYCLE_TRANCHE,
            "fast_recycle_max_hold_minutes": FAST_RECYCLE_MAX_HOLD_MINUTES,
            "fast_recycle_max_position_dollars": FAST_RECYCLE_MAX_POSITION_DOLLARS,
            "fast_recycle_max_open_positions": FAST_RECYCLE_MAX_OPEN_POSITIONS,
            "use_only_fast_recycle_bucket": USE_ONLY_FAST_RECYCLE_BUCKET,
        },
        "broker_state": broker_capital_state_to_dict(broker_state),
        "fast_recycle": _fast_recycle_state_to_dict(fast_recycle),
        "near_market_close": _is_near_market_close(),
        "outside_market_hours": _is_outside_market_hours(),
    }
