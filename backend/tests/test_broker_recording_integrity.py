"""
Recovery-board Track A (Commander, 2026-09-10): an exception after
adapter.place_order() succeeds must never be reported as "skipped before
broker submission" — that is false, and was the actual production defect
(116 of 169 recorded equities failures were exactly this). Also verifies
the duplicate-order protection: a deterministic, per-packet
client_order_id so a genuine retry of the same intended order reuses its
identity rather than risking a second real order.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.integration.brokerage.base import TradeOrder, TradeResult
from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.alert import Alert, AlertPriority, AlertType
from app.models.budget import AllocationCategory, AllocationStatus, BudgetAllocation
from app.models.decision import ActionState, ExecutionPath, OpportunityDecision
from app.models.income_source import IncomeSource, SourceStatus
from app.models.provider_execution import ProviderExecution
from app.services import execution as execution_svc


def _make_session() -> Session:
    import app.models.action_packet  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.budget  # noqa: F401
    import app.models.decision  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.execution_outcome  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.position_lifecycle  # noqa: F401
    import app.models.provider_execution  # noqa: F401
    import app.models.strategy  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_packet(session: Session) -> tuple[IncomeSource, ActionPacket, BudgetAllocation]:
    source = IncomeSource(
        source_id="at:recording-integrity-001",
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
        execution_state=ExecutionState.planned,
    )
    from app.models.budget import WeeklyBudget

    budget = WeeklyBudget(
        week_start_date=date(2026, 9, 8),
        week_end_date=date(2026, 9, 14),
        starting_budget=100.0,
        remaining_budget=85.0,
        starting_bankroll=100.0,
        current_bankroll=100.0,
        evaluation_start_date=date(2026, 9, 8),
        evaluation_end_date=date(2026, 9, 14),
    )
    allocation = BudgetAllocation(
        weekly_budget_id=1,
        allocation_name="NVDA trade",
        category=AllocationCategory.trading,
        amount_allocated=15.0,
        rationale="test",
        source_id=source.source_id,
        approval_required=False,
        approved_by_commander=True,
        status=AllocationStatus.planned,
    )
    session.add(source)
    session.add(packet)
    session.add(budget)
    session.commit()
    allocation.weekly_budget_id = budget.id
    session.add(allocation)
    session.commit()
    session.refresh(source)
    session.refresh(packet)
    session.refresh(allocation)
    return source, packet, allocation


def _fake_result(order: TradeOrder) -> TradeResult:
    return TradeResult(
        order_id="alpaca-order-abc123",
        symbol=order.symbol,
        qty=0.1,
        side=order.side,
        status="filled",
        filled_qty=0.1,
        notional=order.notional,
        raw={"id": "alpaca-order-abc123"},
    )


def test_client_order_id_is_deterministic_per_packet(monkeypatch):
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    captured_orders = []

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            captured_orders.append(order)
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")
    execution_svc.submit_packet_trade(packet.id, order, session)

    assert captured_orders[0].client_order_id == f"hunter-pkt-{packet.id}"


def test_recording_failure_after_broker_success_raises_distinct_error(monkeypatch):
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    from app.services import position_lifecycle as lifecycle_svc

    def _boom(*a, **k):
        raise RuntimeError("simulated local recording crash")

    monkeypatch.setattr(lifecycle_svc, "record_entry_submission", _boom)

    order = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")

    with pytest.raises(execution_svc.BrokerAcceptedRecordingIncompleteError) as excinfo:
        execution_svc.submit_packet_trade(packet.id, order, session)

    assert excinfo.value.order_id == "alpaca-order-abc123"
    assert excinfo.value.status == "filled"

    # ProviderExecution is committed BEFORE the lifecycle-recording step
    # that fails in this test — that commit is real and durable, so the
    # broker acceptance is not lost even though a later bookkeeping step
    # (lifecycle tracking) crashed. This is the desired behavior: the
    # most important record survives; only the exception classification
    # changes so callers don't misread this as "nothing happened."
    rows = session.exec(select(ProviderExecution)).all()
    assert len(rows) == 1
    assert rows[0].external_order_id == "alpaca-order-abc123"


def test_marking_function_never_sets_failed_or_canceled(monkeypatch):
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)
    packet.execution_state = ExecutionState.in_progress
    session.add(packet)
    session.commit()

    exc = execution_svc.BrokerAcceptedRecordingIncompleteError(
        "broker accepted, recording failed",
        order_id="alpaca-order-xyz",
        status="filled",
        symbol="NVDA",
    )

    execution_svc._mark_packet_broker_accepted_recording_incomplete(packet, session, exc)

    session.refresh(packet)
    assert packet.execution_state == ExecutionState.in_progress  # unchanged — NOT failed/canceled
    assert "BROKER_ACCEPTED_RECORDING_INCOMPLETE" in packet.execution_notes
    assert "alpaca-order-xyz" in packet.execution_notes


def test_marking_function_raises_a_critical_alert(monkeypatch):
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    exc = execution_svc.BrokerAcceptedRecordingIncompleteError(
        "broker accepted, recording failed",
        order_id="alpaca-order-xyz",
        status="filled",
        symbol="NVDA",
    )

    execution_svc._mark_packet_broker_accepted_recording_incomplete(packet, session, exc)

    alerts = session.exec(select(Alert)).all()
    assert len(alerts) == 1
    assert alerts[0].priority == AlertPriority.critical
    assert alerts[0].alert_type == AlertType.execution_failed
    assert "alpaca-order-xyz" in alerts[0].body


def test_notional_below_alpaca_minimum_is_rejected_before_broker_submission(monkeypatch):
    """Track A item 4: all 36 recorded error-42210000 rejections were
    Alpaca's own "notional amount must be >= 1.00" — a fixed,
    always-rejected floor Hunter was submitting below. This must now be
    caught locally, honestly, and BEFORE place_order() is ever called —
    not as a wasted round-trip to the broker."""
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    calls = []

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            calls.append(order)
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=0.50, order_type="market", time_in_force="day")

    with pytest.raises(ValueError, match="below Alpaca's \\$1.00 minimum"):
        execution_svc.submit_packet_trade(packet.id, order, session)

    assert calls == []  # never reached the broker


def test_notional_at_or_above_alpaca_minimum_still_submits(monkeypatch):
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=1.00, order_type="market", time_in_force="day")
    result = execution_svc.submit_packet_trade(packet.id, order, session)

    assert result.order_id == "alpaca-order-abc123"


class APIError(Exception):
    """Named APIError so execution.py's type(exc).__name__ == "APIError"
    duck-typed check matches, without importing the real alpaca-py
    exception class — mirrors how a genuine broker HTTP-response error
    surfaces (a response WAS received)."""


def test_place_order_network_failure_raises_ambiguous_not_failed(monkeypatch):
    """Track A item 5: broker-accepts-then-client-loses-response. A
    non-APIError exception from place_order() (timeout, connection
    reset) means no response was received — Hunter cannot assert the
    order was never created, so this must route to the distinct
    ambiguous-outcome path, never a plain "skipped" failure."""
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")

    with pytest.raises(execution_svc.AmbiguousSubmissionOutcomeError) as excinfo:
        execution_svc.submit_packet_trade(packet.id, order, session)

    assert excinfo.value.client_order_id == f"hunter-pkt-{packet.id}"
    assert excinfo.value.packet_id == packet.id


def test_duplicate_client_order_id_api_error_raises_ambiguous(monkeypatch):
    """A broker response that says this client_order_id was already
    used is strong evidence an order already exists under it — this
    must also route to the ambiguous-outcome path, not a plain
    "rejected, safe to retry as new" failure."""
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            raise APIError('{"code":40910000,"message":"client order id must be unique"}')

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")

    with pytest.raises(execution_svc.AmbiguousSubmissionOutcomeError):
        execution_svc.submit_packet_trade(packet.id, order, session)


def test_genuine_api_rejection_still_propagates_as_a_real_failure(monkeypatch):
    """An APIError that's an ordinary validation rejection (nothing
    about a duplicate client_order_id) means the broker definitely did
    NOT create an order — this must still propagate normally so the
    caller can honestly mark the packet failed."""
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            raise APIError('{"code":40310000,"message":"asset AAAA is not tradable"}')

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")

    with pytest.raises(APIError):
        execution_svc.submit_packet_trade(packet.id, order, session)


def test_auto_place_trade_routes_ambiguous_outcome_to_reconciliation_not_skip(monkeypatch):
    session = _make_session()
    source, packet, allocation = _seed_packet(session)
    source.notes = "symbol: NVDA | side: buy"
    session.add(source)
    decision = OpportunityDecision(
        source_id=source.source_id,
        action_state=ActionState.auto_execute,
        execution_path=ExecutionPath.trading,
        execution_ready=True,
        approval_required=False,
    )
    session.add(decision)
    session.commit()

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            raise TimeoutError("read timed out")

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    result = execution_svc.auto_place_trade_for_source(source.source_id, session)

    assert result is None
    session.refresh(packet)
    assert "SUBMISSION_OUTCOME_UNCERTAIN" in (packet.execution_notes or "")
    assert "Trade skipped before broker submission" not in (packet.execution_notes or "")
    assert packet.execution_state not in (ExecutionState.failed, ExecutionState.canceled)

    alerts = session.exec(select(Alert)).all()
    assert any(a.priority == AlertPriority.critical for a in alerts)


def test_retry_after_ambiguous_outcome_reuses_the_same_client_order_id(monkeypatch):
    """The safety net for an uncertain outcome isn't local state — it's
    that any retry (automatic or manual) for the same packet always
    derives the identical client_order_id, so the broker's own
    idempotency check prevents a second real order regardless of what
    Hunter's local bookkeeping believes happened."""
    session = _make_session()
    _source, packet, _alloc = _seed_packet(session)

    captured: list[str] = []

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            captured.append(order.client_order_id)
            raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    order1 = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")
    order2 = TradeOrder(symbol="NVDA", side="buy", notional=15.0, order_type="market", time_in_force="day")

    for order in (order1, order2):
        with pytest.raises(execution_svc.AmbiguousSubmissionOutcomeError):
            execution_svc.submit_packet_trade(packet.id, order, session)

    assert len(captured) == 2
    assert captured[0] == captured[1] == f"hunter-pkt-{packet.id}"


def test_duplicate_dispatch_for_an_already_submitted_packet_places_only_one_order(monkeypatch):
    """Track A item 5: duplicate-worker-claim-or-scheduler-dispatch. Two
    calls to auto_place_trade_for_source for the same source — as would
    happen if a worker reclaim or overlapping scheduler fire dispatched
    the same source twice — must place at most one real order. The
    first call's successful ProviderExecution row is what the second
    call's existing_order guard catches (this is the ordinary case
    where recording succeeded; the recording-failure case is instead
    protected by the deterministic client_order_id, covered above)."""
    session = _make_session()
    source, packet, allocation = _seed_packet(session)
    source.notes = "symbol: NVDA | side: buy"
    session.add(source)
    decision = OpportunityDecision(
        source_id=source.source_id,
        action_state=ActionState.auto_execute,
        execution_path=ExecutionPath.trading,
        execution_ready=True,
        approval_required=False,
    )
    session.add(decision)
    session.commit()

    calls = []

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            calls.append(order)
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    first = execution_svc.auto_place_trade_for_source(source.source_id, session)
    second = execution_svc.auto_place_trade_for_source(source.source_id, session)

    assert first is not None
    assert second is None  # skipped — existing_order guard caught it
    assert len(calls) == 1  # only one real broker order placed

    rows = session.exec(select(ProviderExecution)).all()
    assert len(rows) == 1


def test_auto_place_trade_routes_recording_failure_to_reconciliation_not_skip(monkeypatch):
    """End-to-end through auto_place_trade_for_source: confirms the new
    except-branch is actually reached and produces the distinct
    BROKER_ACCEPTED_RECORDING_INCOMPLETE marking, never the ordinary
    "Trade skipped before broker submission" framing."""
    session = _make_session()
    source, packet, allocation = _seed_packet(session)
    source.notes = "symbol: NVDA | side: buy"
    session.add(source)
    decision = OpportunityDecision(
        source_id=source.source_id,
        action_state=ActionState.auto_execute,
        execution_path=ExecutionPath.trading,
        execution_ready=True,
        approval_required=False,
    )
    session.add(decision)
    session.commit()

    class _FakeAdapter:
        def place_order(self, order: TradeOrder) -> TradeResult:
            return _fake_result(order)

    monkeypatch.setattr(execution_svc, "get_alpaca_adapter", lambda: _FakeAdapter())

    from app.services import position_lifecycle as lifecycle_svc

    monkeypatch.setattr(
        lifecycle_svc, "record_entry_submission",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )

    result = execution_svc.auto_place_trade_for_source(source.source_id, session)

    assert result is None
    session.refresh(packet)
    assert "BROKER_ACCEPTED_RECORDING_INCOMPLETE" in (packet.execution_notes or "")
    assert "Trade skipped before broker submission" not in (packet.execution_notes or "")
    assert packet.execution_state not in (ExecutionState.failed, ExecutionState.canceled)
