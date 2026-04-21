from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.config import ALPACA_ENABLED, FAST_RECYCLE_MAX_HOLD_MINUTES, MAX_HOLD_MINUTES
from app.integration.brokerage.alpaca import get_alpaca_adapter
from app.models.position_lifecycle import PositionLifecycle

logger = logging.getLogger(__name__)


def record_entry_submission(
    session: Session,
    *,
    symbol: str,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    allocation_id: Optional[int] = None,
    provider_order_id: Optional[str] = None,
    entered_at: Optional[datetime] = None,
    max_hold_minutes: Optional[float] = None,
    capital_bucket: str = "legacy",
    execution_profile: Optional[str] = None,
    commit: bool = True,
) -> PositionLifecycle:
    now = _coerce_dt(entered_at) or datetime.now(timezone.utc)
    lifecycle = _find_latest_open_lifecycle(
        session,
        symbol=symbol,
        source_id=source_id,
        packet_id=packet_id,
        allocation_id=allocation_id,
    )
    if lifecycle is None:
        lifecycle = PositionLifecycle(
            symbol=symbol.upper(),
            source_id=source_id,
            packet_id=packet_id,
            allocation_id=allocation_id,
            entered_at=now,
            capital_bucket=capital_bucket,
            execution_profile=execution_profile,
            max_hold_minutes=float(max_hold_minutes or MAX_HOLD_MINUTES),
        )
    lifecycle.status = "open"
    lifecycle.source_id = source_id or lifecycle.source_id
    lifecycle.packet_id = packet_id or lifecycle.packet_id
    lifecycle.allocation_id = allocation_id or lifecycle.allocation_id
    lifecycle.capital_bucket = capital_bucket or lifecycle.capital_bucket or "legacy"
    lifecycle.execution_profile = execution_profile or lifecycle.execution_profile
    lifecycle.entered_at = lifecycle.entered_at or now
    lifecycle.entry_order_id = provider_order_id or lifecycle.entry_order_id
    lifecycle.max_hold_minutes = float(lifecycle.max_hold_minutes or max_hold_minutes or MAX_HOLD_MINUTES)
    lifecycle.updated_at = datetime.now(timezone.utc)
    session.add(lifecycle)
    if commit:
        session.commit()
        session.refresh(lifecycle)
    else:
        session.flush()
    return lifecycle


def record_exit_submission(
    session: Session,
    *,
    symbol: str,
    provider_order_id: Optional[str] = None,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    submitted_at: Optional[datetime] = None,
    stale_reason: Optional[str] = None,
    exit_reason: Optional[str] = None,
    commit: bool = True,
) -> Optional[PositionLifecycle]:
    lifecycle = _find_latest_open_lifecycle(
        session,
        symbol=symbol,
        source_id=source_id,
        packet_id=packet_id,
    )
    if lifecycle is None:
        return None
    now = _coerce_dt(submitted_at) or datetime.now(timezone.utc)
    lifecycle.exit_submitted_at = lifecycle.exit_submitted_at or now
    lifecycle.exit_order_id = provider_order_id or lifecycle.exit_order_id
    lifecycle.exit_reason = exit_reason or lifecycle.exit_reason
    if stale_reason:
        lifecycle.stale_marked_at = lifecycle.stale_marked_at or now
        lifecycle.stale_reason = stale_reason
    lifecycle.updated_at = datetime.now(timezone.utc)
    session.add(lifecycle)
    if commit:
        session.commit()
        session.refresh(lifecycle)
    else:
        session.flush()
    return lifecycle


def close_lifecycle_for_execution(
    session: Session,
    *,
    symbol: Optional[str] = None,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    actual_return: Optional[float] = None,
    exited_at: Optional[datetime] = None,
    exit_reason: Optional[str] = None,
    commit: bool = True,
) -> Optional[PositionLifecycle]:
    lifecycle = _find_latest_open_lifecycle(
        session,
        symbol=symbol,
        source_id=source_id,
        packet_id=packet_id,
    )
    if lifecycle is None:
        return None

    now = _coerce_dt(exited_at) or datetime.now(timezone.utc)
    lifecycle.exited_at = now
    lifecycle.status = "closed"
    lifecycle.realized_pl = actual_return
    lifecycle.exit_reason = exit_reason or lifecycle.exit_reason
    _compute_closed_metrics(lifecycle)
    lifecycle.updated_at = datetime.now(timezone.utc)
    session.add(lifecycle)
    if commit:
        session.commit()
        session.refresh(lifecycle)
    else:
        session.flush()
    return lifecycle


def sync_lifecycles_with_broker_state(session: Session, broker_state) -> None:
    now = datetime.now(timezone.utc)
    open_symbols: set[str] = set()

    for position in broker_state.positions:
        symbol = (position.symbol or "").upper()
        if not symbol:
            continue
        open_symbols.add(symbol)
        lifecycle = _find_latest_open_lifecycle(session, symbol=symbol)
        if lifecycle is None:
            lifecycle = PositionLifecycle(symbol=symbol)

        broker_entry_at = _derive_entry_time(now, position.hold_minutes)
        lifecycle.status = "open"
        lifecycle.entered_at = lifecycle.entered_at or broker_entry_at
        lifecycle.entry_filled_at = lifecycle.entry_filled_at or broker_entry_at
        lifecycle.hold_duration_minutes = position.hold_minutes
        lifecycle.max_hold_minutes = float(
            lifecycle.max_hold_minutes
            or (
                FAST_RECYCLE_MAX_HOLD_MINUTES
                if lifecycle.capital_bucket == "fast_recycle"
                else MAX_HOLD_MINUTES
            )
        )
        if (
            lifecycle.max_hold_minutes is not None
            and position.hold_minutes is not None
            and position.hold_minutes >= lifecycle.max_hold_minutes
        ):
            lifecycle.stale_marked_at = lifecycle.stale_marked_at or now
            lifecycle.stale_reason = lifecycle.stale_reason or "max_hold_exceeded"
        lifecycle.updated_at = now
        session.add(lifecycle)

    open_with_exit_pending = session.exec(
        select(PositionLifecycle).where(
            PositionLifecycle.status == "open",
            PositionLifecycle.exit_submitted_at.is_not(None),
        )
    ).all()
    for lifecycle in open_with_exit_pending:
        if lifecycle.symbol.upper() in open_symbols:
            continue
        lifecycle.exited_at = lifecycle.exited_at or now
        lifecycle.status = "closed"
        _compute_closed_metrics(lifecycle)
        lifecycle.updated_at = now
        session.add(lifecycle)

    session.commit()


def reconcile_order_fills_with_broker(
    session: Session,
    *,
    broker_state=None,
    commit: bool = True,
) -> list[dict]:
    if not ALPACA_ENABLED:
        return []

    try:
        adapter = get_alpaca_adapter()
    except Exception as exc:
        logger.debug("reconcile_order_fills_with_broker: adapter unavailable: %s", exc)
        return []

    open_symbols = {
        (position.symbol or "").upper()
        for position in getattr(broker_state, "positions", []) or []
        if getattr(position, "symbol", None)
    } if broker_state is not None else None
    now = datetime.now(timezone.utc)
    reconciled: list[dict] = []
    open_lifecycles = list(
        session.exec(
            select(PositionLifecycle).where(PositionLifecycle.status == "open")
        ).all()
    )

    for lifecycle in open_lifecycles:
        entry_order = _safe_get_order(adapter, lifecycle.entry_order_id)
        exit_order = _safe_get_order(adapter, lifecycle.exit_order_id)

        if entry_order and lifecycle.entry_filled_at is None and _order_is_filled(entry_order):
            lifecycle.entry_filled_at = (
                _extract_order_time(entry_order, "filled_at")
                or _extract_order_time(entry_order, "submitted_at")
                or lifecycle.entered_at
                or now
            )
            lifecycle.updated_at = now
            session.add(lifecycle)

        should_close = False
        close_time = None
        realized_pl = lifecycle.realized_pl

        if exit_order and _order_is_filled(exit_order):
            should_close = True
            close_time = (
                _extract_order_time(exit_order, "filled_at")
                or _extract_order_time(exit_order, "submitted_at")
                or now
            )
            realized_pl = _compute_realized_pl(lifecycle, entry_order, exit_order)
        elif lifecycle.exit_order_id and open_symbols is not None and lifecycle.symbol.upper() not in open_symbols:
            should_close = True
            close_time = now

        if not should_close:
            continue

        lifecycle.exited_at = lifecycle.exited_at or close_time or now
        lifecycle.status = "closed"
        lifecycle.realized_pl = realized_pl
        _compute_closed_metrics(lifecycle)
        lifecycle.updated_at = now
        session.add(lifecycle)
        reconciled.append(
            {
                "symbol": lifecycle.symbol,
                "packet_id": lifecycle.packet_id,
                "source_id": lifecycle.source_id,
                "realized_pl": lifecycle.realized_pl,
                "exited_at": _iso(lifecycle.exited_at),
            }
        )

    if commit:
        session.commit()
    else:
        session.flush()

    return reconciled


def serialize_lifecycle(lifecycle: Optional[PositionLifecycle]) -> Optional[dict]:
    if lifecycle is None:
        return None
    return {
        "id": lifecycle.id,
        "symbol": lifecycle.symbol,
        "source_id": lifecycle.source_id,
        "packet_id": lifecycle.packet_id,
        "allocation_id": lifecycle.allocation_id,
        "status": lifecycle.status,
        "capital_bucket": lifecycle.capital_bucket,
        "execution_profile": lifecycle.execution_profile,
        "entered_at": _iso(lifecycle.entered_at),
        "entry_filled_at": _iso(lifecycle.entry_filled_at),
        "exit_submitted_at": _iso(lifecycle.exit_submitted_at),
        "exited_at": _iso(lifecycle.exited_at),
        "first_profitable_at": _iso(lifecycle.first_profitable_at),
        "stale_marked_at": _iso(lifecycle.stale_marked_at),
        "stale_reason": lifecycle.stale_reason,
        "exit_reason": lifecycle.exit_reason,
        "hold_duration_minutes": lifecycle.hold_duration_minutes,
        "time_to_realized_profit_minutes": lifecycle.time_to_realized_profit_minutes,
        "max_hold_minutes": lifecycle.max_hold_minutes,
        "realized_pl": lifecycle.realized_pl,
        "entry_order_id": lifecycle.entry_order_id,
        "exit_order_id": lifecycle.exit_order_id,
    }


def get_recent_closed_lifecycles(session: Session, limit: int = 10) -> list[PositionLifecycle]:
    return list(
        session.exec(
            select(PositionLifecycle)
            .where(PositionLifecycle.status == "closed")
            .order_by(PositionLifecycle.exited_at.desc(), PositionLifecycle.updated_at.desc())
            .limit(limit)
        ).all()
    )


def get_latest_lifecycle(
    session: Session,
    *,
    symbol: Optional[str] = None,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    capital_bucket: Optional[str] = None,
) -> Optional[PositionLifecycle]:
    statement = select(PositionLifecycle)
    if packet_id is not None:
        statement = statement.where(PositionLifecycle.packet_id == packet_id)
    if source_id:
        statement = statement.where(PositionLifecycle.source_id == source_id)
    if symbol:
        statement = statement.where(PositionLifecycle.symbol == symbol.upper())
    if capital_bucket:
        statement = statement.where(PositionLifecycle.capital_bucket == capital_bucket)
    statement = statement.order_by(PositionLifecycle.updated_at.desc(), PositionLifecycle.created_at.desc())
    return _exec_first_safe(session, statement)


def enrich_broker_positions_with_lifecycle(session: Session, positions: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for position in positions:
        symbol = (position.get("symbol") or "").upper()
        lifecycle = _find_latest_open_lifecycle(session, symbol=symbol) if symbol else None
        current_hold = position.get("hold_minutes")
        max_hold = lifecycle.max_hold_minutes if lifecycle and lifecycle.max_hold_minutes is not None else MAX_HOLD_MINUTES
        enriched.append(
            {
                **position,
                "capital_bucket": lifecycle.capital_bucket if lifecycle else "legacy",
                "execution_profile": lifecycle.execution_profile if lifecycle else None,
                "entered_at": _iso(lifecycle.entered_at) if lifecycle else None,
                "entry_filled_at": _iso(lifecycle.entry_filled_at) if lifecycle else None,
                "stale_marked_at": _iso(lifecycle.stale_marked_at) if lifecycle else None,
                "stale_reason": lifecycle.stale_reason if lifecycle else None,
                "max_hold_minutes": max_hold,
                "over_max_hold": bool(current_hold is not None and max_hold is not None and current_hold >= max_hold),
            }
        )
    return enriched


def get_lifecycles_by_bucket(
    session: Session,
    *,
    capital_bucket: str,
    status: Optional[str] = None,
) -> list[PositionLifecycle]:
    statement = select(PositionLifecycle).where(PositionLifecycle.capital_bucket == capital_bucket)
    if status:
        statement = statement.where(PositionLifecycle.status == status)
    statement = statement.order_by(PositionLifecycle.updated_at.desc(), PositionLifecycle.created_at.desc())
    return _exec_all_safe(session, statement)


def _find_latest_open_lifecycle(
    session: Session,
    *,
    symbol: Optional[str] = None,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    allocation_id: Optional[int] = None,
) -> Optional[PositionLifecycle]:
    statement = select(PositionLifecycle).where(PositionLifecycle.status == "open")
    if symbol:
        statement = statement.where(PositionLifecycle.symbol == symbol.upper())
    if packet_id is not None:
        statement = statement.where(PositionLifecycle.packet_id == packet_id)
    if source_id:
        statement = statement.where(PositionLifecycle.source_id == source_id)
    if allocation_id is not None:
        statement = statement.where(PositionLifecycle.allocation_id == allocation_id)
    statement = statement.order_by(PositionLifecycle.updated_at.desc(), PositionLifecycle.created_at.desc())
    return _exec_first_safe(session, statement)


def _exec_first_safe(session: Session, statement):
    try:
        return session.exec(statement).first()
    except OperationalError as exc:
        if _is_missing_table_error(exc):
            return None
        raise


def _exec_all_safe(session: Session, statement) -> list:
    try:
        return list(session.exec(statement).all())
    except OperationalError as exc:
        if _is_missing_table_error(exc):
            return []
        raise


def _is_missing_table_error(exc: OperationalError) -> bool:
    return "no such table" in str(exc).lower()


def _compute_closed_metrics(lifecycle: PositionLifecycle) -> None:
    entry_time = _coerce_dt(lifecycle.entry_filled_at or lifecycle.entered_at)
    exit_time = _coerce_dt(lifecycle.exited_at)
    if entry_time and exit_time:
        lifecycle.hold_duration_minutes = round(max(0.0, (exit_time - entry_time).total_seconds() / 60.0), 2)
        if lifecycle.realized_pl is not None and lifecycle.realized_pl > 0:
            lifecycle.time_to_realized_profit_minutes = lifecycle.hold_duration_minutes


def _safe_get_order(adapter, order_id: Optional[str]):
    if not order_id:
        return None
    try:
        return adapter.get_order(order_id)
    except Exception as exc:
        logger.debug("position_lifecycle: failed to fetch order %s: %s", order_id, exc)
        return None


def _order_is_filled(order) -> bool:
    status = str(getattr(order, "status", "") or "").lower()
    return status in {"filled", "partially_filled"}


def _extract_order_time(order, field_name: str) -> Optional[datetime]:
    raw = getattr(order, "raw", None) or {}
    if isinstance(raw, dict):
        raw_value = raw.get(field_name)
        parsed = _parse_datetime(raw_value)
        if parsed is not None:
            return parsed
    if field_name == "submitted_at":
        return _parse_datetime(getattr(order, "submitted_at", None))
    return None


def _parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _coerce_dt(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _coerce_dt(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def _compute_realized_pl(
    lifecycle: PositionLifecycle,
    entry_order,
    exit_order,
) -> Optional[float]:
    entry_price = float(getattr(entry_order, "filled_avg_price", 0.0) or 0.0) if entry_order else 0.0
    exit_price = float(getattr(exit_order, "filled_avg_price", 0.0) or 0.0) if exit_order else 0.0
    qty = float(
        getattr(exit_order, "filled_qty", 0.0)
        or getattr(entry_order, "filled_qty", 0.0)
        or 0.0
    )
    if entry_price <= 0 or exit_price <= 0 or qty <= 0:
        return lifecycle.realized_pl

    entry_side = str(getattr(entry_order, "side", "buy") or "buy").lower() if entry_order else "buy"
    if entry_side == "sell":
        gross_pl = (entry_price - exit_price) * qty
    else:
        gross_pl = (exit_price - entry_price) * qty
    return round(gross_pl, 2)


def _derive_entry_time(now: datetime, hold_minutes: Optional[float]) -> datetime:
    if hold_minutes is None:
        return now
    return now - timedelta(minutes=max(0.0, float(hold_minutes)))


def _coerce_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
