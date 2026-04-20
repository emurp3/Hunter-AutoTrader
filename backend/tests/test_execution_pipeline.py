from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.budget import AllocationCategory, AllocationStatus, BudgetAllocation, WeeklyBudget
from app.models.decision import ActionState, ExecutionPath, OpportunityDecision
from app.models.execution_outcome import ExecutionOutcome
from app.models.income_source import IncomeSource, SourceStatus
from app.models.provider_execution import ProviderExecution
from app.models.task import Task


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
    import app.models.task  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_trading_source(session: Session, *, notes: str = "symbol: NVDA | side: buy") -> tuple[IncomeSource, ActionPacket]:
    source = IncomeSource(
        source_id="at:nvda-001",
        description="NVDA breakout setup",
        estimated_profit=6.5,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 4, 20),
        next_action="Submit trade",
        notes=notes,
        origin_module="autotrader",
        category="trading",
        confidence=0.82,
        score=88.0,
        priority_band="high",
    )
    packet = ActionPacket(
        source_id=source.source_id,
        opportunity_summary=source.description,
        score=source.score,
        priority_band=source.priority_band,
        estimated_return=source.estimated_profit,
        budget_recommendation=15.0,
        status=PacketStatus.ready,
        execution_state=ExecutionState.planned,
    )
    decision = OpportunityDecision(
        source_id=source.source_id,
        action_state=ActionState.auto_execute,
        execution_path=ExecutionPath.trading,
        score_at_decision=source.score,
        confidence_at_decision=source.confidence,
        execution_ready=True,
        approval_required=False,
        capital_recommendation=15.0,
    )
    budget = WeeklyBudget(
        week_start_date=date(2026, 4, 20),
        week_end_date=date(2026, 4, 26),
        starting_budget=100.0,
        remaining_budget=85.0,
        starting_bankroll=100.0,
        current_bankroll=100.0,
        evaluation_start_date=date(2026, 4, 20),
        evaluation_end_date=date(2026, 4, 26),
    )
    allocation = BudgetAllocation(
        weekly_budget_id=1,
        allocation_name="NVDA trade",
        category=AllocationCategory.trading,
        amount_allocated=15.0,
        rationale="AutoTrader signal",
        expected_return=6.5,
        source_id=source.source_id,
        approval_required=False,
        approved_by_commander=True,
        status=AllocationStatus.planned,
    )
    session.add(source)
    session.add(packet)
    session.add(decision)
    session.add(budget)
    session.commit()
    allocation.weekly_budget_id = budget.id
    session.add(allocation)
    session.commit()
    session.refresh(source)
    session.refresh(packet)
    return source, packet


def test_trading_sources_do_not_dispatch_worker_tasks() -> None:
    from app.services.tasks import auto_dispatch_for_source

    with _make_session() as session:
        source, _packet = _seed_trading_source(session)

        task = auto_dispatch_for_source(source.source_id, session)

        assert task is None
        assert session.exec(select(Task)).all() == []


def test_auto_place_trade_routes_through_packet_execution(monkeypatch) -> None:
    from app.services import execution as execution_svc

    with _make_session() as session:
        source, packet = _seed_trading_source(session)

        monkeypatch.setattr(execution_svc.alert_svc, "raise_alert", lambda *args, **kwargs: None)
        monkeypatch.setattr(execution_svc.event_svc, "log_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            execution_svc,
            "get_alpaca_adapter",
            lambda: SimpleNamespace(
                place_order=lambda order: SimpleNamespace(
                    order_id="ord-123",
                    symbol=order.symbol,
                    side=order.side,
                    qty=None,
                    notional=order.notional,
                    status="accepted",
                    provider_message=None,
                    raw={"id": "ord-123"},
                )
            ),
        )

        result = execution_svc.auto_place_trade_for_source(source.source_id, session)

        assert result is not None
        assert result.order_id == "ord-123"

        packet = session.get(ActionPacket, packet.id)
        assert packet.execution_state == ExecutionState.in_progress

        provider_execs = session.exec(
            select(ProviderExecution).where(ProviderExecution.packet_id == packet.id)
        ).all()
        assert len(provider_execs) == 1

        status = execution_svc.get_execution_status(session)
        assert status["counts"]["active"] == 1
        assert status["counts"]["completed"] == 0
        assert status["counts"]["failed"] == 0


def test_auto_place_trade_cancels_dead_end_packet_with_reason(monkeypatch) -> None:
    from app.services import execution as execution_svc
    from app.services import budget as budget_svc

    with _make_session() as session:
        source, packet = _seed_trading_source(session, notes="missing explicit ticker")

        monkeypatch.setattr(execution_svc.alert_svc, "raise_alert", lambda *args, **kwargs: None)
        monkeypatch.setattr(execution_svc.event_svc, "log_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(budget_svc, "record_capital_release", lambda *args, **kwargs: None)
        monkeypatch.setattr(budget_svc, "record_capital_failure", lambda *args, **kwargs: None)

        result = execution_svc.auto_place_trade_for_source(source.source_id, session)

        assert result is None

        packet = session.get(ActionPacket, packet.id)
        assert packet.execution_state == ExecutionState.canceled
        assert "Trade skipped before broker submission" in (packet.execution_notes or "")

        outcomes = session.exec(
            select(ExecutionOutcome).where(ExecutionOutcome.action_packet_id == packet.id)
        ).all()
        assert len(outcomes) == 1
        assert "No trade symbol found" in (outcomes[0].failure_reason or "")
