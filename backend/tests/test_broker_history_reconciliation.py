"""
Track A item 3 (Commander, 2026-09-10): reconcile historical broker
activity without resubmitting, matching by exact identifier only —
never by timestamp/symbol/quantity similarity — and never fabricating
attribution for unmatched activity.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.integration.brokerage.base import TradeResult
from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.income_source import IncomeSource, SourceStatus
from app.models.position_lifecycle import PositionLifecycle
from app.models.provider_execution import ProviderExecution
from app.services import broker_history_reconciliation as recon_svc


def _make_session() -> Session:
    import app.models.action_packet  # noqa: F401
    import app.models.decision  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.position_lifecycle  # noqa: F401
    import app.models.provider_execution  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_packet(session: Session, packet_id_hint: int | None = None) -> ActionPacket:
    source = IncomeSource(
        source_id=f"at:recon-{packet_id_hint or 'x'}",
        description="NVDA breakout",
        estimated_profit=6.5,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 9, 8),
        origin_module="autotrader",
        category="trading",
        confidence=0.82,
        score=88.0,
    )
    packet = ActionPacket(
        source_id=source.source_id,
        opportunity_summary=source.description,
        status=PacketStatus.ready,
        execution_state=ExecutionState.in_progress,
    )
    session.add(source)
    session.add(packet)
    session.commit()
    session.refresh(packet)
    return packet


def _order(order_id: str, *, client_order_id: str | None = None, symbol: str = "NVDA", status: str = "filled") -> TradeResult:
    return TradeResult(
        order_id=order_id,
        symbol=symbol,
        qty=1.0,
        side="buy",
        status=status,
        filled_qty=1.0,
        submitted_at="2026-09-09T14:30:00+00:00",
        raw={"id": order_id, "client_order_id": client_order_id, "symbol": symbol},
    )


class _FakeAdapter:
    def __init__(self, orders: list[TradeResult]) -> None:
        self._orders = orders

    def list_orders_paginated(self, *, max_pages: int, page_size: int) -> list[TradeResult]:
        return self._orders


def test_matches_packet_based_order_by_exact_provider_execution_id(monkeypatch):
    session = _make_session()
    packet = _seed_packet(session)
    session.add(
        ProviderExecution(
            packet_id=packet.id,
            source_id=packet.source_id,
            provider="alpaca",
            external_order_id="order-already-recorded",
            symbol="NVDA",
            order_side="buy",
            order_type="market",
            execution_status="filled",
        )
    )
    session.commit()

    monkeypatch.setattr(
        recon_svc, "get_alpaca_adapter", lambda: _FakeAdapter([_order("order-already-recorded")])
    )

    result = recon_svc.reconcile_broker_history(session)

    assert result["matched_packet_based"] == 1
    assert result["matched_recycle"] == 0
    assert result["restored_count"] == 0
    assert result["unmatched_count"] == 0


def test_matches_recycle_order_by_exact_lifecycle_order_id(monkeypatch):
    session = _make_session()
    session.add(PositionLifecycle(symbol="NVDA", status="closed", entry_order_id="recycle-order-1"))
    session.commit()

    monkeypatch.setattr(recon_svc, "get_alpaca_adapter", lambda: _FakeAdapter([_order("recycle-order-1")]))

    result = recon_svc.reconcile_broker_history(session)

    assert result["matched_recycle"] == 1
    assert result["matched_packet_based"] == 0
    assert result["restored_count"] == 0
    assert result["unmatched_count"] == 0


def test_restores_missing_record_only_when_hunter_assigned_the_client_order_id(monkeypatch):
    session = _make_session()
    packet = _seed_packet(session)

    monkeypatch.setattr(
        recon_svc,
        "get_alpaca_adapter",
        lambda: _FakeAdapter([_order("new-broker-order", client_order_id=f"hunter-pkt-{packet.id}")]),
    )

    result = recon_svc.reconcile_broker_history(session)

    assert result["restored_count"] == 1
    assert result["restored"][0]["packet_id"] == packet.id
    assert result["unmatched_count"] == 0

    rows = session.exec(select(ProviderExecution).where(ProviderExecution.external_order_id == "new-broker-order")).all()
    assert len(rows) == 1
    assert rows[0].reconciled_from_broker_history is True
    assert rows[0].reconciliation_marker is not None
    assert rows[0].packet_id == packet.id


def test_never_attributes_by_similarity_alone_stays_unmatched(monkeypatch):
    """An order with the same symbol as a real packet, but no Hunter-
    assigned client_order_id, must never be auto-attributed — it stays
    unmatched."""
    session = _make_session()
    _seed_packet(session)  # same symbol "NVDA", but no linking identifier

    monkeypatch.setattr(
        recon_svc,
        "get_alpaca_adapter",
        lambda: _FakeAdapter([_order("mystery-order", client_order_id="some-random-uuid")]),
    )

    result = recon_svc.reconcile_broker_history(session)

    assert result["restored_count"] == 0
    assert result["unmatched_count"] == 1
    assert result["unmatched"][0]["order_id"] == "mystery-order"

    rows = session.exec(select(ProviderExecution)).all()
    assert rows == []


def test_reconciliation_is_idempotent_when_run_twice(monkeypatch):
    session = _make_session()
    packet = _seed_packet(session)
    adapter = _FakeAdapter([_order("dup-check-order", client_order_id=f"hunter-pkt-{packet.id}")])
    monkeypatch.setattr(recon_svc, "get_alpaca_adapter", lambda: adapter)

    first = recon_svc.reconcile_broker_history(session)
    second = recon_svc.reconcile_broker_history(session)

    assert first["restored_count"] == 1
    assert second["restored_count"] == 0
    assert second["matched_packet_based"] == 1

    rows = session.exec(select(ProviderExecution).where(ProviderExecution.external_order_id == "dup-check-order")).all()
    assert len(rows) == 1  # no duplicate import


def test_uncertain_outcome_packets_are_counted(monkeypatch):
    session = _make_session()
    packet = _seed_packet(session)
    packet.execution_notes = "BROKER_ACCEPTED_RECORDING_INCOMPLETE: order_id=xyz status=filled symbol=NVDA — boom"
    session.add(packet)
    session.commit()

    monkeypatch.setattr(recon_svc, "get_alpaca_adapter", lambda: _FakeAdapter([]))

    result = recon_svc.reconcile_broker_history(session)

    assert result["internal_packets_with_uncertain_outcomes"] == 1
