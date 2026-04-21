from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Optional

from sqlmodel import Session, select

from app.models.position_lifecycle import PositionLifecycle
from app.services import budget as budget_svc


def build_daily_report(session: Session, *, now: Optional[datetime] = None) -> dict:
    now = _coerce_now(now)
    start = _day_start(now)
    end = start + timedelta(days=1)
    return _build_report(session, label="daily", start=start, end=end, now=now)


def build_weekly_report(session: Session, *, now: Optional[datetime] = None) -> dict:
    now = _coerce_now(now)
    start = _week_start(now)
    end = start + timedelta(days=7)
    return _build_report(session, label="weekly", start=start, end=end, now=now)


def _build_report(
    session: Session,
    *,
    label: str,
    start: datetime,
    end: datetime,
    now: datetime,
) -> dict:
    lifecycles = list(session.exec(select(PositionLifecycle)).all())
    open_lifecycles = [row for row in lifecycles if row.status == "open"]
    closed_in_period = [row for row in lifecycles if _in_window(row.exited_at, start, end)]
    opened_in_period = [
        row for row in lifecycles if _in_window(_entry_timestamp(row), start, end)
    ]
    stale_in_period = [row for row in lifecycles if _in_window(row.stale_marked_at, start, end)]
    current_over_max_hold = [row for row in open_lifecycles if _is_over_max_hold(row, now)]

    hold_values = _valid_numbers(row.hold_duration_minutes for row in closed_in_period)
    profit_timing_values = _valid_numbers(
        row.time_to_realized_profit_minutes
        for row in closed_in_period
        if (row.realized_pl or 0.0) > 0
    )
    profitable_closes = [row for row in closed_in_period if (row.realized_pl or 0.0) > 0]
    losing_closes = [row for row in closed_in_period if (row.realized_pl or 0.0) < 0]
    realized_pl = round(sum((row.realized_pl or 0.0) for row in closed_in_period), 2)

    capital_state = _safe_capital_state(session)
    unrealized_snapshot = capital_state.get("unrealized_pl")

    fast_closed = [row for row in closed_in_period if row.capital_bucket == "fast_recycle"]
    legacy_closed = [row for row in closed_in_period if row.capital_bucket != "fast_recycle"]

    report = {
        "period": label,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "generated_at": now.isoformat(),
        "timing": {
            "trades_opened": len(opened_in_period),
            "trades_closed": len(closed_in_period),
            "profitable_closes": len(profitable_closes),
            "losing_closes": len(losing_closes),
            "win_rate": _ratio(len(profitable_closes), len(profitable_closes) + len(losing_closes)),
            "average_hold_time_minutes": _average(hold_values),
            "median_hold_time_minutes": _median(hold_values),
            "average_time_to_realized_profit_minutes": _average(profit_timing_values),
            "fastest_time_to_realized_profit_minutes": min(profit_timing_values) if profit_timing_values else None,
            "slowest_time_to_realized_profit_minutes": max(profit_timing_values) if profit_timing_values else None,
            "realized_pl": realized_pl,
            "unrealized_pl_snapshot": round(float(unrealized_snapshot), 2) if unrealized_snapshot is not None else None,
            "stale_positions": len(stale_in_period),
            "positions_over_max_hold": len(current_over_max_hold),
            "capital_reuse_count": len(closed_in_period),
        },
        "fast_recycle": _bucket_report(
            rows=closed_in_period,
            open_rows=open_lifecycles,
            bucket="fast_recycle",
            now=now,
        ),
        "legacy": _bucket_report(
            rows=closed_in_period,
            open_rows=open_lifecycles,
            bucket="legacy",
            now=now,
        ),
        "open_position_snapshot": {
            "open_positions_count": len(open_lifecycles),
            "stale_open_positions_count": len(
                [row for row in open_lifecycles if row.stale_marked_at is not None or _is_over_max_hold(row, now)]
            ),
            "positions_over_max_hold_count": len(current_over_max_hold),
            "positions": [_serialize_open_position(row, now) for row in open_lifecycles],
        },
    }

    if label == "weekly":
        report["timing"]["total_fast_recycle_closes"] = len(fast_closed)
        report["timing"]["total_legacy_closes"] = len(legacy_closed)

    return report


def _bucket_report(
    *,
    rows: list[PositionLifecycle],
    open_rows: list[PositionLifecycle],
    bucket: str,
    now: datetime,
) -> dict:
    bucket_rows = [row for row in rows if _bucket_match(row, bucket)]
    bucket_open = [row for row in open_rows if _bucket_match(row, bucket)]
    holds = _valid_numbers(row.hold_duration_minutes for row in bucket_rows)
    profit_timing = _valid_numbers(
        row.time_to_realized_profit_minutes for row in bucket_rows if (row.realized_pl or 0.0) > 0
    )
    profitable = [row for row in bucket_rows if (row.realized_pl or 0.0) > 0]
    losing = [row for row in bucket_rows if (row.realized_pl or 0.0) < 0]
    return {
        "bucket": bucket,
        "trades_closed": len(bucket_rows),
        "profitable_closes": len(profitable),
        "losing_closes": len(losing),
        "win_rate": _ratio(len(profitable), len(profitable) + len(losing)),
        "realized_pl": round(sum((row.realized_pl or 0.0) for row in bucket_rows), 2),
        "average_hold_time_minutes": _average(holds),
        "median_hold_time_minutes": _median(holds),
        "average_time_to_realized_profit_minutes": _average(profit_timing),
        "fastest_time_to_realized_profit_minutes": min(profit_timing) if profit_timing else None,
        "slowest_time_to_realized_profit_minutes": max(profit_timing) if profit_timing else None,
        "capital_reuse_count": len(bucket_rows),
        "open_positions_count": len(bucket_open),
        "positions_over_max_hold": len([row for row in bucket_open if _is_over_max_hold(row, now)]),
        "stale_positions": len([row for row in bucket_open if row.stale_marked_at is not None]),
        "execution_profiles": dict(Counter(row.execution_profile or "unknown" for row in bucket_rows)),
    }


def _serialize_open_position(row: PositionLifecycle, now: datetime) -> dict:
    current_hold = _current_hold_minutes(row, now)
    max_hold = row.max_hold_minutes
    return {
        "symbol": row.symbol,
        "capital_bucket": row.capital_bucket,
        "execution_profile": row.execution_profile,
        "entered_at": _iso(row.entered_at),
        "entry_filled_at": _iso(row.entry_filled_at),
        "stale_marked_at": _iso(row.stale_marked_at),
        "stale_reason": row.stale_reason,
        "hold_duration_minutes": current_hold,
        "max_hold_minutes": max_hold,
        "over_max_hold": bool(
            current_hold is not None and max_hold is not None and current_hold >= max_hold
        ),
    }


def _safe_capital_state(session: Session) -> dict:
    try:
        return budget_svc.get_broker_reconciled_capital_state(session)
    except Exception:
        return {}


def _bucket_match(row: PositionLifecycle, bucket: str) -> bool:
    if bucket == "legacy":
        return row.capital_bucket != "fast_recycle"
    return row.capital_bucket == bucket


def _entry_timestamp(row: PositionLifecycle) -> Optional[datetime]:
    return row.entry_filled_at or row.entered_at


def _current_hold_minutes(row: PositionLifecycle, now: datetime) -> Optional[float]:
    start = row.entry_filled_at or row.entered_at
    if start is None:
        return row.hold_duration_minutes
    return round((now - _coerce_dt(start)).total_seconds() / 60.0, 2)


def _is_over_max_hold(row: PositionLifecycle, now: datetime) -> bool:
    current_hold = _current_hold_minutes(row, now)
    return bool(
        current_hold is not None
        and row.max_hold_minutes is not None
        and current_hold >= row.max_hold_minutes
    )


def _valid_numbers(values) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            result.append(round(float(value), 2))
        except (TypeError, ValueError):
            continue
    return result


def _average(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(float(median(values)), 2)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def _in_window(value: Optional[datetime], start: datetime, end: datetime) -> bool:
    if value is None:
        return False
    ts = _coerce_dt(value)
    return start <= ts < end


def _coerce_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_now(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return _coerce_dt(value)


def _day_start(value: datetime) -> datetime:
    dt = _coerce_dt(value)
    return datetime.combine(dt.date(), time.min, tzinfo=timezone.utc)


def _week_start(value: datetime) -> datetime:
    dt = _day_start(value)
    return dt - timedelta(days=dt.weekday())


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _coerce_dt(value).isoformat()
