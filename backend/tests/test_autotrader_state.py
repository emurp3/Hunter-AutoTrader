from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


def _make_session() -> Session:
    import app.models.income_source  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_refresh_intake_state_marks_ready_file_source(monkeypatch) -> None:
    from app.services import autotrader as at

    monkeypatch.setattr(
        at,
        "assess_live_source",
        lambda: at.SourceSnapshot(
            source_type="file",
            status="ready",
            message="AutoTrader file is fresh.",
            path="C:/fake/autotrader.json",
            updated_at="2026-04-20T10:00:00+00:00",
            record_count=1,
        ),
    )

    state = at.refresh_intake_state()

    assert state.last_source_type == "file"
    assert state.live_data_status == "ready"
    assert state.current_data_mode == "live"
    assert state.live_data_record_count == 1


def test_bootstrap_intake_runs_initial_file_ingest(monkeypatch) -> None:
    from app.services import autotrader as at

    sentinel = object()
    monkeypatch.setattr(
        at,
        "refresh_intake_state",
        lambda bootstrap_file_source=False: SimpleNamespace(
            last_source_type="file",
            live_data_status="ready",
            last_scan_at=None,
        ),
    )
    monkeypatch.setattr(at, "run_intake", lambda session: sentinel)

    with _make_session() as session:
        result = at.bootstrap_intake(session)

    assert result is sentinel
