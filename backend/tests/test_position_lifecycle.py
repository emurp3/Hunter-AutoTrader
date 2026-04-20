from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.position_lifecycle import PositionLifecycle
from app.services import position_lifecycle as lifecycle_svc


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def test_entry_timestamp_persisted():
    with _make_session() as session:
        entered_at = datetime(2026, 4, 20, 13, 0, tzinfo=timezone.utc)
        lifecycle = lifecycle_svc.record_entry_submission(
            session,
            symbol="AAPL",
            source_id="at:aapl-001",
            provider_order_id="buy-1",
            entered_at=entered_at,
        )

        stored = session.get(PositionLifecycle, lifecycle.id)
        assert stored is not None
        assert _as_utc(stored.entered_at) == entered_at
        assert stored.entry_order_id == "buy-1"
        assert stored.status == "open"


def test_exit_timestamp_persisted():
    with _make_session() as session:
        entry = lifecycle_svc.record_entry_submission(
            session,
            symbol="AAPL",
            source_id="at:aapl-001",
            provider_order_id="buy-1",
            entered_at=datetime(2026, 4, 20, 13, 0, tzinfo=timezone.utc),
        )
        exit_time = datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc)
        lifecycle = lifecycle_svc.record_exit_submission(
            session,
            symbol="AAPL",
            source_id="at:aapl-001",
            provider_order_id="sell-1",
            submitted_at=exit_time,
        )

        stored = session.get(PositionLifecycle, entry.id)
        assert lifecycle is not None
        assert stored is not None
        assert _as_utc(stored.exit_submitted_at) == exit_time
        assert stored.exit_order_id == "sell-1"


def test_hold_duration_minutes_computed_on_close():
    with _make_session() as session:
        entry_time = datetime(2026, 4, 20, 13, 0, tzinfo=timezone.utc)
        lifecycle_svc.record_entry_submission(
            session,
            symbol="MSFT",
            source_id="at:msft-001",
            provider_order_id="buy-2",
            entered_at=entry_time,
        )
        lifecycle = lifecycle_svc.get_latest_lifecycle(session, symbol="MSFT", source_id="at:msft-001")
        lifecycle.entry_filled_at = entry_time
        session.add(lifecycle)
        session.commit()

        closed = lifecycle_svc.close_lifecycle_for_execution(
            session,
            symbol="MSFT",
            source_id="at:msft-001",
            actual_return=-1.25,
            exited_at=entry_time + timedelta(minutes=95),
        )

        assert closed is not None
        assert closed.status == "closed"
        assert closed.hold_duration_minutes == 95.0
        assert closed.time_to_realized_profit_minutes is None


def test_time_to_realized_profit_minutes_computed_on_profitable_close():
    with _make_session() as session:
        entry_time = datetime(2026, 4, 20, 9, 30, tzinfo=timezone.utc)
        lifecycle_svc.record_entry_submission(
            session,
            symbol="NVDA",
            source_id="at:nvda-001",
            packet_id=7,
            provider_order_id="buy-3",
            entered_at=entry_time,
        )
        lifecycle = lifecycle_svc.get_latest_lifecycle(session, symbol="NVDA", packet_id=7)
        lifecycle.entry_filled_at = entry_time
        session.add(lifecycle)
        session.commit()

        closed = lifecycle_svc.close_lifecycle_for_execution(
            session,
            symbol="NVDA",
            packet_id=7,
            actual_return=12.5,
            exited_at=entry_time + timedelta(minutes=42),
        )

        assert closed is not None
        assert closed.hold_duration_minutes == 42.0
        assert closed.time_to_realized_profit_minutes == 42.0
        assert closed.realized_pl == 12.5
