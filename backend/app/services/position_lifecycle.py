from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.config import MAX_HOLD_MINUTES
from app.models.position_lifecycle import PositionLifecycle


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
            max_hold_minutes=float(max_hold_minutes or MAX_HOLD_MINUTES),
        )
    lifecycle.status = "open"
    lifecycle.source_id = source_id or lifecycle.source_id
    lifecycle.packet_id = packet_id or lifecycle.packet_id
    lifecycle.allocation_id = allocation_id or lifecycle.allocation_id
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
        lifecycle.max_hold_minutes = float(lifecycle.max_hold_minutes or MAX_HOLD_MINUTES)
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
        "entered_at": _iso(lifecycle.entered_at),
        "entry_filled_at": _iso(lifecycle.entry_filled_at),
        "exit_submitted_at": _iso(lifecycle.exit_submitted_at),
        "exited_at": _iso(lifecycle.exited_at),
        "first_profitable_at": _iso(lifecycle.first_profitable_at),
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
) -> Optional[PositionLifecycle]:
    statement = select(PositionLifecycle)
    if packet_id is not None:
        statement = statement.where(PositionLifecycle.packet_id == packet_id)
    if source_id:
        statement = statement.where(PositionLifecycle.source_id == source_id)
    if symbol:
        statement = statement.where(PositionLifecycle.symbol == symbol.upper())
    statement = statement.order_by(PositionLifecycle.updated_at.desc(), PositionLifecycle.created_at.desc())
    return session.exec(statement).first()


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
                "entered_at": _iso(lifecycle.entered_at) if lifecycle else None,
                "entry_filled_at": _iso(lifecycle.entry_filled_at) if lifecycle else None,
                "max_hold_minutes": max_hold,
                "over_max_hold": bool(current_hold is not None and max_hold is not None and current_hold >= max_hold),
            }
        )
    return enriched


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
    return session.exec(statement).first()


def _compute_closed_metrics(lifecycle: PositionLifecycle) -> None:
    entry_time = _coerce_dt(lifecycle.entry_filled_at or lifecycle.entered_at)
    exit_time = _coerce_dt(lifecycle.exited_at)
    if entry_time and exit_time:
        lifecycle.hold_duration_minutes = round(max(0.0, (exit_time - entry_time).total_seconds() / 60.0), 2)
        if lifecycle.realized_pl is not None and lifecycle.realized_pl > 0:
            lifecycle.time_to_realized_profit_minutes = lifecycle.hold_duration_minutes


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
