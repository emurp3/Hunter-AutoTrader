from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.position_lifecycle import PositionLifecycle
from app.services import reporting as reporting_svc


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add_lifecycle(session: Session, **overrides) -> PositionLifecycle:
    row = PositionLifecycle(
        symbol=overrides.pop("symbol", "AAPL"),
        status=overrides.pop("status", "closed"),
        capital_bucket=overrides.pop("capital_bucket", "legacy"),
        execution_profile=overrides.pop("execution_profile", None),
        entered_at=overrides.pop("entered_at", None),
        entry_filled_at=overrides.pop("entry_filled_at", None),
        exit_submitted_at=overrides.pop("exit_submitted_at", None),
        exited_at=overrides.pop("exited_at", None),
        stale_marked_at=overrides.pop("stale_marked_at", None),
        stale_reason=overrides.pop("stale_reason", None),
        hold_duration_minutes=overrides.pop("hold_duration_minutes", None),
        time_to_realized_profit_minutes=overrides.pop("time_to_realized_profit_minutes", None),
        max_hold_minutes=overrides.pop("max_hold_minutes", None),
        realized_pl=overrides.pop("realized_pl", None),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_daily_reporting_aggregates_timing_and_bucket_metrics(monkeypatch):
    now = datetime(2026, 4, 22, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        reporting_svc.budget_svc,
        "get_broker_reconciled_capital_state",
        lambda session: {"unrealized_pl": 8.75},
    )

    with _make_session() as session:
        _add_lifecycle(
            session,
            symbol="NVDA",
            capital_bucket="fast_recycle",
            execution_profile="FAST_RECYCLE",
            entered_at=now - timedelta(minutes=35),
            entry_filled_at=now - timedelta(minutes=30),
            exited_at=now - timedelta(minutes=1),
            hold_duration_minutes=30,
            time_to_realized_profit_minutes=30,
            realized_pl=12.5,
        )
        _add_lifecycle(
            session,
            symbol="MSFT",
            capital_bucket="legacy",
            entered_at=now - timedelta(hours=3),
            entry_filled_at=now - timedelta(minutes=90),
            exited_at=now - timedelta(minutes=5),
            hold_duration_minutes=90,
            time_to_realized_profit_minutes=None,
            realized_pl=-4.0,
        )
        _add_lifecycle(
            session,
            symbol="AMD",
            status="open",
            capital_bucket="fast_recycle",
            execution_profile="FAST_RECYCLE",
            entry_filled_at=now - timedelta(minutes=80),
            stale_marked_at=now - timedelta(minutes=10),
            stale_reason="max_hold_exceeded",
            max_hold_minutes=45,
        )

        report = reporting_svc.build_daily_report(session, now=now)

    timing = report["timing"]
    assert timing["trades_opened"] == 3
    assert timing["trades_closed"] == 2
    assert timing["profitable_closes"] == 1
    assert timing["losing_closes"] == 1
    assert timing["average_hold_time_minutes"] == 60.0
    assert timing["median_hold_time_minutes"] == 60.0
    assert timing["average_time_to_realized_profit_minutes"] == 30.0
    assert timing["fastest_time_to_realized_profit_minutes"] == 30.0
    assert timing["slowest_time_to_realized_profit_minutes"] == 30.0
    assert timing["realized_pl"] == 8.5
    assert timing["unrealized_pl_snapshot"] == 8.75
    assert timing["stale_positions"] == 1
    assert timing["positions_over_max_hold"] == 1
    assert timing["capital_reuse_count"] == 2
    assert report["fast_recycle"]["trades_closed"] == 1
    assert report["legacy"]["trades_closed"] == 1


def test_weekly_reporting_separates_fast_recycle_and_legacy(monkeypatch):
    now = datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        reporting_svc.budget_svc,
        "get_broker_reconciled_capital_state",
        lambda session: {"unrealized_pl": None},
    )

    with _make_session() as session:
        _add_lifecycle(
            session,
            symbol="TSLA",
            capital_bucket="fast_recycle",
            execution_profile="FAST_RECYCLE",
            entered_at=now - timedelta(days=1, minutes=50),
            entry_filled_at=now - timedelta(days=1, minutes=45),
            exited_at=now - timedelta(days=1),
            hold_duration_minutes=45,
            time_to_realized_profit_minutes=45,
            realized_pl=7.0,
        )
        _add_lifecycle(
            session,
            symbol="META",
            capital_bucket="legacy",
            entered_at=now - timedelta(days=2, hours=4),
            entry_filled_at=now - timedelta(days=2, hours=3),
            exited_at=now - timedelta(days=2),
            hold_duration_minutes=180,
            time_to_realized_profit_minutes=None,
            realized_pl=-3.5,
        )
        _add_lifecycle(
            session,
            symbol="AMZN",
            capital_bucket="legacy",
            entered_at=now - timedelta(days=4, minutes=30),
            entry_filled_at=now - timedelta(days=4, minutes=25),
            exited_at=now - timedelta(days=4),
            hold_duration_minutes=25,
            time_to_realized_profit_minutes=25,
            realized_pl=5.0,
            stale_marked_at=now - timedelta(days=4, minutes=5),
        )

        report = reporting_svc.build_weekly_report(session, now=now)

    timing = report["timing"]
    assert timing["trades_closed"] == 3
    assert timing["profitable_closes"] == 2
    assert timing["losing_closes"] == 1
    assert timing["win_rate"] == 0.667
    assert timing["average_hold_time_minutes"] == 83.33
    assert timing["median_hold_time_minutes"] == 45.0
    assert timing["average_time_to_realized_profit_minutes"] == 35.0
    assert timing["fastest_time_to_realized_profit_minutes"] == 25.0
    assert timing["slowest_time_to_realized_profit_minutes"] == 45.0
    assert timing["realized_pl"] == 8.5
    assert timing["stale_positions"] == 1
    assert timing["capital_reuse_count"] == 3
    assert timing["total_fast_recycle_closes"] == 1
    assert timing["total_legacy_closes"] == 2
    assert report["fast_recycle"]["realized_pl"] == 7.0
    assert report["legacy"]["realized_pl"] == 1.5
