"""
Execution service.

This module serves two roles:
1. brokerage passthrough helpers for trade execution
2. Hunter packet execution lifecycle management
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Optional

from sqlmodel import Session, select

from app.config import (
    ALPACA_API_KEY,
    ALPACA_BASE_URL,
    ALPACA_EFFECTIVE_SOURCES,
    ALPACA_ENABLED,
    ALPACA_PAPER,
    ALPACA_SECRET_KEY,
    ENV_LOAD_DIAGNOSTICS,
    EXECUTION_PROVIDER,
)
from app.integration.brokerage.alpaca import get_alpaca_adapter
from app.integration.brokerage.base import AccountInfo, PositionInfo, TradeOrder, TradeResult
from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.alert import AlertPriority, AlertType
from app.models.budget import AllocationStatus, BudgetAllocation, BudgetOutcome, WeeklyBudget
from app.models.event import EventType
from app.models.execution_outcome import ExecutionOutcome
from app.models.income_source import IncomeSource, SourceStatus
from app.models.provider_execution import ProviderExecution
from app.models.strategy import Strategy, StrategyStatus
from app.services import alerts as alert_svc
from app.services import events as event_svc


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ExecutionState.planned: {
        ExecutionState.active,
        ExecutionState.canceled,
        ExecutionState.failed,
    },
    ExecutionState.active: {
        ExecutionState.in_progress,
        ExecutionState.completed,
        ExecutionState.failed,
        ExecutionState.canceled,
    },
    ExecutionState.in_progress: {
        ExecutionState.completed,
        ExecutionState.failed,
        ExecutionState.canceled,
    },
    ExecutionState.completed: set(),
    ExecutionState.failed: set(),
    ExecutionState.canceled: set(),
}


def get_execution_provider_status(session: Session) -> dict:
    provider = EXECUTION_PROVIDER or "none"
    base_status = {
        "provider": provider,
        "enabled": ALPACA_ENABLED if provider == "alpaca" else False,
        "mode": "paper" if ALPACA_PAPER else "live",
        "api_key_detected": bool(ALPACA_API_KEY),
        "secret_key_detected": bool(ALPACA_SECRET_KEY),
        "base_url": ALPACA_BASE_URL,
        "env_diagnostics": get_execution_provider_diagnostics(),
    }
    if provider != "alpaca":
        return {**base_status, "mode": "disabled", "connected": False, "detail": f"Unsupported execution provider: {provider}"}

    if not ALPACA_ENABLED:
        return {**base_status, "connected": False, "detail": "Alpaca execution is disabled. Set ALPACA_ENABLED=true to activate."}

    try:
        adapter = get_alpaca_adapter()
        status = adapter.get_account_status()
        status.update(base_status)
        status["recent_order_count"] = len(get_provider_orders(session, limit=20))
        status["open_position_count"] = len(adapter.get_positions())
        return status
    except EnvironmentError as exc:
        return {
            **base_status,
            "connected": False,
            "error_type": exc.__class__.__name__,
            "detail": str(exc),
        }
    except Exception as exc:
        return {
            **base_status,
            "connected": False,
            "error_type": exc.__class__.__name__,
            "detail": _format_provider_error(exc),
        }


def get_execution_provider_diagnostics() -> dict:
    loaded_files = [item["path"] for item in ENV_LOAD_DIAGNOSTICS if item["loaded"]]
    return {
        "checked_files": [item["path"] for item in ENV_LOAD_DIAGNOSTICS],
        "loaded_files": loaded_files,
        "last_loaded_file": loaded_files[-1] if loaded_files else None,
        "file_details": ENV_LOAD_DIAGNOSTICS,
        "runtime": {
            "provider": EXECUTION_PROVIDER or "none",
            "alpaca_enabled": ALPACA_ENABLED,
            "alpaca_paper": ALPACA_PAPER,
            "base_url": ALPACA_BASE_URL,
            "api_key_detected": bool(ALPACA_API_KEY),
            "secret_key_detected": bool(ALPACA_SECRET_KEY),
            "effective_sources": ALPACA_EFFECTIVE_SOURCES,
        },
    }


def get_provider_account() -> AccountInfo:
    return get_alpaca_adapter().get_balance()


def get_provider_positions() -> list[PositionInfo]:
    return get_alpaca_adapter().get_positions()


def get_provider_orders(session: Session, limit: int = 20) -> list[ProviderExecution]:
    return list(
        session.exec(
            select(ProviderExecution)
            .order_by(ProviderExecution.submitted_at.desc(), ProviderExecution.created_at.desc())
            .limit(limit)
        ).all()
    )


def place_trade(
    order: TradeOrder,
    session: Session,
    *,
    packet_id: Optional[int] = None,
    source_id: Optional[str] = None,
) -> TradeResult:
    if packet_id is not None:
        return submit_packet_trade(packet_id, order, session)

    adapter = get_alpaca_adapter()

    try:
        result = adapter.place_trade(order)
    except Exception as exc:
        if source_id:
            alert_svc.raise_alert(
                alert_type=AlertType.execution_failed,
                title=f"Trade execution failed - {order.symbol}",
                body=str(exc),
                session=session,
                priority=AlertPriority.critical,
                source_id=source_id,
            )
            event_svc.log_event(
                source_id,
                EventType.error,
                session,
                summary=f"Trade failed: {order.symbol} {order.side} {order.qty} - {exc}",
                metadata={"symbol": order.symbol, "side": order.side, "qty": order.qty, "error": str(exc)},
            )
        raise

    if source_id:
        event_svc.log_event(
            source_id,
            EventType.executed,
            session,
            summary=f"Trade placed: {order.symbol} {order.side} {order.qty} - order_id={result.order_id} status={result.status}",
            metadata={
                "order_id": result.order_id,
                "symbol": result.symbol,
                "side": result.side,
                "qty": result.qty,
                "status": result.status,
                "filled_qty": result.filled_qty,
                "filled_avg_price": result.filled_avg_price,
            },
        )
        if result.status not in ("rejected", "canceled"):
            alert_svc.raise_alert(
                alert_type=AlertType.execution_completed,
                title=f"Trade submitted - {order.symbol} {order.side} {order.qty}",
                body=f"order_id={result.order_id} status={result.status}",
                session=session,
                priority=AlertPriority.medium,
                source_id=source_id,
            )

    return result


def get_account() -> AccountInfo:
    adapter = get_alpaca_adapter()
    return adapter.get_account()


def get_order(order_id: str) -> TradeResult:
    adapter = get_alpaca_adapter()
    return adapter.get_order(order_id)


def cancel_order(order_id: str, session: Session, *, source_id: Optional[str] = None) -> bool:
    adapter = get_alpaca_adapter()
    success = adapter.cancel_order(order_id)
    if source_id and success:
        event_svc.log_event(
            source_id,
            EventType.state_change,
            session,
            summary=f"Order cancelled: {order_id}",
        )
    return success


def submit_packet_trade(packet_id: int, order: TradeOrder, session: Session) -> TradeResult:
    packet = _get_packet_or_raise(packet_id, session)
    allocation = _get_allocation(packet.source_id, session)
    if not allocation:
        raise ValueError("Funded allocation is required before execution can be submitted.")
    if allocation.status not in (AllocationStatus.planned, AllocationStatus.active):
        raise ValueError(f"Allocation is not executable in its current state: {allocation.status}")

    existing_order = session.exec(
        select(ProviderExecution)
        .where(
            ProviderExecution.packet_id == packet.id,
            ProviderExecution.execution_status.notin_(["canceled", "rejected"]),
        )
        .order_by(ProviderExecution.created_at.desc())
    ).first()
    if existing_order:
        raise ValueError(
            f"Packet {packet.id} already has a provider order in flight ({existing_order.external_order_id})."
        )

    _validate_order_against_allocation(order, allocation)

    if packet.execution_state == ExecutionState.planned:
        packet = _transition_packet_execution(
            packet,
            ExecutionState.active,
            session,
            notes="Execution provider request opened",
        )
    if packet.execution_state == ExecutionState.active:
        packet = _transition_packet_execution(
            packet,
            ExecutionState.in_progress,
            session,
            notes="Execution provider request submitted",
        )

    adapter = get_alpaca_adapter()
    result = adapter.place_order(order)
    provider_execution = ProviderExecution(
        packet_id=packet.id,
        source_id=packet.source_id,
        allocation_id=allocation.id,
        provider="alpaca",
        provider_mode="paper" if ALPACA_PAPER else "live",
        external_order_id=result.order_id,
        symbol=result.symbol,
        order_side=result.side,
        order_type=order.order_type,
        qty=result.qty or order.qty,
        notional=result.notional or order.notional,
        limit_price=order.limit_price,
        submitted_at=datetime.now(timezone.utc),
        execution_status=result.status,
        provider_message=result.provider_message,
        raw_response_json=json.dumps(result.raw) if result.raw else None,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(provider_execution)
    session.commit()
    session.refresh(provider_execution)

    event_svc.log_event(
        packet.source_id,
        EventType.executed,
        session,
        summary=f"Order submitted via Alpaca ({'paper' if ALPACA_PAPER else 'live'}) for packet {packet.id}",
        metadata={
            "packet_id": packet.id,
            "allocation_id": allocation.id,
            "provider": "alpaca",
            "mode": "paper" if ALPACA_PAPER else "live",
            "external_order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side,
            "qty": result.qty,
            "notional": result.notional,
            "status": result.status,
        },
    )
    alert_svc.raise_alert(
        alert_type=AlertType.execution_completed,
        title=f"Order submitted ({'paper' if ALPACA_PAPER else 'live'}) - packet {packet.id}",
        body=f"Alpaca {'paper ' if ALPACA_PAPER else 'live '}order {result.order_id} for {result.symbol} is {result.status}.",
        session=session,
        priority=AlertPriority.medium,
        source_id=packet.source_id,
    )
    return result


def start_packet_execution(packet_id: int, session: Session, *, notes: Optional[str] = None) -> ActionPacket:
    packet = _get_packet_or_raise(packet_id, session)
    next_state = (
        ExecutionState.active
        if packet.execution_state == ExecutionState.planned
        else ExecutionState.in_progress
    )
    return _transition_packet_execution(packet, next_state, session, notes=notes)


def complete_packet_execution(
    packet_id: int,
    session: Session,
    *,
    actual_return: Optional[float] = None,
    success_reason: Optional[str] = None,
    notes: Optional[str] = None,
) -> ActionPacket:
    packet = _get_packet_or_raise(packet_id, session)
    return _transition_packet_execution(
        packet,
        ExecutionState.completed,
        session,
        actual_return=actual_return,
        success_reason=success_reason,
        notes=notes,
    )


def fail_packet_execution(
    packet_id: int,
    session: Session,
    *,
    actual_return: Optional[float] = None,
    failure_reason: Optional[str] = None,
    notes: Optional[str] = None,
    canceled: bool = False,
) -> ActionPacket:
    packet = _get_packet_or_raise(packet_id, session)
    target_state = ExecutionState.canceled if canceled else ExecutionState.failed
    return _transition_packet_execution(
        packet,
        target_state,
        session,
        actual_return=actual_return,
        failure_reason=failure_reason,
        notes=notes,
    )


def get_execution_status(session: Session) -> dict:
    packets = session.exec(
        select(ActionPacket).where(ActionPacket.execution_state != ExecutionState.planned)
    ).all()
    outcomes = session.exec(
        select(ExecutionOutcome).order_by(ExecutionOutcome.recorded_at.desc())
    ).all()

    active_packets = [p for p in packets if p.execution_state in (ExecutionState.active, ExecutionState.in_progress)]
    completed_packets = [p for p in packets if p.execution_state == ExecutionState.completed]
    failed_packets = [p for p in packets if p.execution_state in (ExecutionState.failed, ExecutionState.canceled)]

    return {
        "active_executions": [_packet_payload(p, session) for p in active_packets],
        "completed_executions": [_packet_payload(p, session) for p in completed_packets],
        "failed_executions": [_packet_payload(p, session) for p in failed_packets],
        "recent_outcomes": [
            {
                "packet_id": outcome.action_packet_id,
                "source_id": outcome.source_id,
                "strategy_id": outcome.strategy_id,
                "lane": outcome.lane,
                "category": outcome.category,
                "execution_state": outcome.execution_state,
                "actual_return": outcome.actual_return,
                "time_to_completion_hours": outcome.time_to_completion_hours,
                "success_reason": outcome.success_reason,
                "failure_reason": outcome.failure_reason,
                "notes": outcome.notes,
                "recorded_at": outcome.recorded_at.isoformat(),
            }
            for outcome in outcomes[:20]
        ],
        "counts": {
            "active": len(active_packets),
            "completed": len(completed_packets),
            "failed": len(failed_packets),
        },
    }


def get_packet_execution_payload(packet_id: int, session: Session) -> dict:
    packet = _get_packet_or_raise(packet_id, session)
    return _packet_payload(packet, session)


def _transition_packet_execution(
    packet: ActionPacket,
    target_state: str,
    session: Session,
    *,
    actual_return: Optional[float] = None,
    success_reason: Optional[str] = None,
    failure_reason: Optional[str] = None,
    notes: Optional[str] = None,
) -> ActionPacket:
    old_state = packet.execution_state or ExecutionState.planned
    if target_state not in _ALLOWED_TRANSITIONS.get(old_state, set()):
        raise ValueError(f"Invalid execution transition: {old_state} -> {target_state}")

    now = datetime.now(timezone.utc)
    packet.execution_state = target_state
    packet.execution_updated_at = now
    packet.execution_notes = notes or packet.execution_notes

    if target_state in (ExecutionState.active, ExecutionState.in_progress):
        packet.execution_started_at = packet.execution_started_at or now
    elif target_state == ExecutionState.completed:
        packet.execution_completed_at = now
        packet.status = PacketStatus.executed
    elif target_state == ExecutionState.failed:
        packet.execution_failed_at = now
    elif target_state == ExecutionState.canceled:
        packet.execution_canceled_at = now

    session.add(packet)

    source = _get_source(packet.source_id, session)
    strategy = _get_strategy(packet.source_id, session)
    allocation = _get_allocation(packet.source_id, session)

    if target_state in (ExecutionState.active, ExecutionState.in_progress):
        _activate_allocation(allocation, session, now)
        if source:
            source.status = SourceStatus.active
            session.add(source)
        if strategy and strategy.status == StrategyStatus.candidate:
            strategy.status = StrategyStatus.active
            strategy.updated_at = now
            session.add(strategy)

    if target_state == ExecutionState.completed:
        _complete_allocation(allocation, session, now)
        if source:
            source.status = SourceStatus.outcome_logged
            session.add(source)
        if strategy:
            strategy.status = StrategyStatus.completed
            strategy.updated_at = now
            if actual_return is not None:
                strategy.actual_return = round((strategy.actual_return or 0.0) + actual_return, 2)
            strategy.reason_for_continuation_or_termination = success_reason or "Execution completed"
            session.add(strategy)
        _record_outcome(
            packet=packet,
            allocation=allocation,
            source=source,
            strategy=strategy,
            session=session,
            state=target_state,
            actual_return=actual_return,
            success_reason=success_reason,
            failure_reason=failure_reason,
            notes=notes,
            now=now,
        )
        if allocation and actual_return is not None:
            _record_budget_outcome(
                allocation,
                packet,
                source,
                strategy,
                session,
                actual_return=actual_return,
                success_reason=success_reason,
                failure_reason=failure_reason,
                notes=notes,
                now=now,
            )
            from app.services import budget as budget_svc

            budget_svc.record_capital_completion(
                session,
                allocation,
                source_id=packet.source_id,
                action_packet_id=packet.id,
                actual_return=actual_return,
                notes=notes,
            )

    if target_state in (ExecutionState.failed, ExecutionState.canceled):
        _release_or_flag_allocation(allocation, session, now, canceled=target_state == ExecutionState.canceled)
        if source:
            source.status = SourceStatus.failed
            session.add(source)
        if strategy:
            strategy.status = StrategyStatus.failed
            strategy.updated_at = now
            strategy.reason_for_continuation_or_termination = failure_reason or "Execution failed"
            session.add(strategy)
        _record_outcome(
            packet=packet,
            allocation=allocation,
            source=source,
            strategy=strategy,
            session=session,
            state=target_state,
            actual_return=actual_return,
            success_reason=success_reason,
            failure_reason=failure_reason,
            notes=notes,
            now=now,
        )
        if allocation:
            from app.services import budget as budget_svc

            if target_state == ExecutionState.canceled:
                budget_svc.record_capital_release(
                    session,
                    allocation,
                    source_id=packet.source_id,
                    action_packet_id=packet.id,
                    notes=notes,
                )
            else:
                budget_svc.record_capital_failure(
                    session,
                    allocation,
                    source_id=packet.source_id,
                    action_packet_id=packet.id,
                    actual_return=actual_return or 0.0,
                    notes=notes,
                )

    session.commit()
    session.refresh(packet)

    event_svc.log_event(
        packet.source_id,
        EventType.state_change,
        session,
        old_state=old_state,
        new_state=target_state,
        summary=f"Execution state changed: {old_state} -> {target_state}",
        metadata={
            "packet_id": packet.id,
            "allocation_id": allocation.id if allocation else None,
            "actual_return": actual_return,
            "success_reason": success_reason,
            "failure_reason": failure_reason,
            "notes": notes,
        },
    )

    if target_state == ExecutionState.completed:
        event_svc.log_event(
            packet.source_id,
            EventType.outcome_logged,
            session,
            summary=f"Execution completed for packet {packet.id}",
            metadata={"actual_return": actual_return, "success_reason": success_reason},
        )
        alert_svc.raise_alert(
            alert_type=AlertType.execution_completed,
            title=f"Execution completed - packet {packet.id}",
            body=success_reason or "Execution completed successfully.",
            session=session,
            priority=AlertPriority.medium,
            source_id=packet.source_id,
        )
    elif target_state in (ExecutionState.failed, ExecutionState.canceled):
        alert_svc.raise_alert(
            alert_type=AlertType.execution_failed,
            title=f"Execution {target_state} - packet {packet.id}",
            body=failure_reason or notes or "Execution failed or was canceled.",
            session=session,
            priority=AlertPriority.high,
            source_id=packet.source_id,
        )

    return packet


def _record_outcome(
    *,
    packet: ActionPacket,
    allocation: Optional[BudgetAllocation],
    source: Optional[IncomeSource],
    strategy: Optional[Strategy],
    session: Session,
    state: str,
    actual_return: Optional[float],
    success_reason: Optional[str],
    failure_reason: Optional[str],
    notes: Optional[str],
    now: datetime,
) -> None:
    started_at = packet.execution_started_at or packet.updated_at or packet.created_at
    completion_hours = max(0.0, round((now - started_at).total_seconds() / 3600, 2))
    outcome = ExecutionOutcome(
        action_packet_id=packet.id,
        allocation_id=allocation.id if allocation else None,
        source_id=packet.source_id,
        strategy_id=strategy.strategy_id if strategy else None,
        lane=_infer_lane(source),
        category=source.category if source else None,
        execution_state=state,
        allocated_amount=allocation.amount_allocated if allocation else None,
        actual_return=actual_return,
        time_to_completion_hours=completion_hours if state == ExecutionState.completed else None,
        success_reason=success_reason,
        failure_reason=failure_reason,
        notes=notes,
    )
    session.add(outcome)


def _record_budget_outcome(
    allocation: BudgetAllocation,
    packet: ActionPacket,
    source: Optional[IncomeSource],
    strategy: Optional[Strategy],
    session: Session,
    *,
    actual_return: float,
    success_reason: Optional[str],
    failure_reason: Optional[str],
    notes: Optional[str],
    now: datetime,
) -> None:
    started_at = allocation.started_at or packet.execution_started_at or allocation.created_at
    completion_hours = max(0.0, round((now - started_at).total_seconds() / 3600, 2))
    budget_outcome = BudgetOutcome(
        allocation_id=allocation.id,
        actual_return=actual_return,
        net_result=round(actual_return - allocation.amount_allocated, 2),
        outcome_notes=notes,
        success_reason=success_reason,
        failure_reason=failure_reason,
        time_to_completion_hours=completion_hours,
        source_id=packet.source_id,
        strategy_id=strategy.strategy_id if strategy else None,
        action_packet_id=packet.id,
        lane=_infer_lane(source),
        category=source.category if source else None,
    )
    session.add(budget_outcome)
    budget = session.get(WeeklyBudget, allocation.weekly_budget_id)
    if budget:
        budget.realized_return = round((budget.realized_return or 0.0) + actual_return, 2)
        session.add(budget)


def _activate_allocation(allocation: Optional[BudgetAllocation], session: Session, now: datetime) -> None:
    if not allocation:
        return
    allocation.status = AllocationStatus.active
    allocation.started_at = allocation.started_at or now
    allocation.updated_at = now
    session.add(allocation)


def _complete_allocation(allocation: Optional[BudgetAllocation], session: Session, now: datetime) -> None:
    if not allocation:
        return
    allocation.status = AllocationStatus.spent
    allocation.completed_at = now
    allocation.updated_at = now
    session.add(allocation)


def _release_or_flag_allocation(
    allocation: Optional[BudgetAllocation],
    session: Session,
    now: datetime,
    *,
    canceled: bool,
) -> None:
    if not allocation:
        return

    budget = session.get(WeeklyBudget, allocation.weekly_budget_id)
    if allocation.status != AllocationStatus.spent and budget:
        budget.remaining_budget = round((budget.remaining_budget or 0.0) + allocation.amount_allocated, 2)
        session.add(budget)

    allocation.status = AllocationStatus.canceled if canceled else AllocationStatus.failed
    allocation.updated_at = now
    if canceled:
        allocation.canceled_at = now
    else:
        allocation.failed_at = now
    session.add(allocation)


def _get_packet_or_raise(packet_id: int, session: Session) -> ActionPacket:
    packet = session.get(ActionPacket, packet_id)
    if not packet:
        raise ValueError(f"Action packet not found: {packet_id}")
    return packet


def _get_source(source_id: str, session: Session) -> Optional[IncomeSource]:
    return session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()


def _get_strategy(source_id: str, session: Session) -> Optional[Strategy]:
    return session.exec(
        select(Strategy).where(Strategy.linked_opportunity_source_id == source_id)
    ).first()


def _get_allocation(source_id: str, session: Session) -> Optional[BudgetAllocation]:
    return session.exec(
        select(BudgetAllocation)
        .where(BudgetAllocation.source_id == source_id)
        .order_by(BudgetAllocation.created_at.desc())
    ).first()


def _validate_order_against_allocation(order: TradeOrder, allocation: BudgetAllocation) -> None:
    if order.notional is None and (order.qty is None or order.limit_price is None):
        raise ValueError(
            "Use notional, or provide qty with limit_price, so Hunter can enforce allocation caps safely."
        )

    requested_exposure = float(order.notional or (order.qty or 0.0) * (order.limit_price or 0.0))
    if requested_exposure <= 0:
        raise ValueError("Requested order exposure must be greater than zero.")
    if requested_exposure > allocation.amount_allocated:
        raise ValueError(
            f"Requested exposure ${requested_exposure:.2f} exceeds allocation cap ${allocation.amount_allocated:.2f}."
        )


def auto_place_trade_for_source(source_id: str, session: Session) -> Optional[TradeResult]:
    """
    Auto-place an Alpaca market order for an execution_ready trading source.
    Called from the orchestrator pipeline — never raises, returns None on skip/failure.

    Guards (all must pass):
      - ALPACA_ENABLED=True
      - Decision exists with execution_ready=True and execution_path="trading"
      - A symbol is extractable from source description/notes
      - A funded allocation or capital_recommendation > 0 is available
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not ALPACA_ENABLED:
        _logger.debug("auto_place_trade: ALPACA_ENABLED=False — skipping %s", source_id)
        return None

    from sqlmodel import select as _select
    from app.models.decision import ExecutionPath, OpportunityDecision

    decision = session.exec(
        _select(OpportunityDecision).where(OpportunityDecision.source_id == source_id)
    ).first()
    if not decision or not decision.execution_ready:
        return None
    if decision.execution_path != ExecutionPath.trading:
        return None

    source = session.exec(
        _select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not source:
        return None

    symbol = _extract_trade_symbol(source)
    if not symbol:
        _logger.warning("auto_place_trade: no symbol found for source %s — skipping", source_id)
        return None

    allocation = _get_allocation(source_id, session)
    notional: Optional[float] = None
    if allocation and allocation.status in (AllocationStatus.planned, AllocationStatus.active):
        notional = float(allocation.amount_allocated)
    elif decision.capital_recommendation and decision.capital_recommendation > 0:
        notional = float(decision.capital_recommendation)

    if not notional or notional <= 0:
        _logger.warning("auto_place_trade: no notional amount for source %s — skipping", source_id)
        return None

    side = _extract_trade_side(source)
    order = TradeOrder(symbol=symbol, side=side, notional=notional, order_type="market", time_in_force="day")

    try:
        result = place_trade(order, session, source_id=source_id)
        _logger.info(
            "auto_place_trade: %s %s $%.2f notional — order_id=%s status=%s mode=%s",
            side.upper(), symbol, notional, result.order_id, result.status,
            "paper" if ALPACA_PAPER else "live",
        )
        return result
    except Exception as exc:
        _logger.warning("auto_place_trade: failed for source %s: %s", source_id, exc)
        return None


def _extract_trade_symbol(source: IncomeSource) -> Optional[str]:
    """Extract a ticker symbol from source description or notes. Returns None if not found."""
    import re
    for text in (source.notes or "", source.description or ""):
        # Explicit key-value: symbol: AAPL  or  ticker: AAPL
        m = re.search(r"\b(?:symbol|ticker)\s*[:=]\s*([A-Z]{1,5})\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        # Dollar-prefixed: $AAPL
        m = re.search(r"\$([A-Z]{1,5})\b", text)
        if m:
            return m.group(1).upper()
    return None


def _extract_trade_side(source: IncomeSource) -> str:
    """Infer trade side from source text. Defaults to 'buy'."""
    text = f"{source.description or ''} {source.notes or ''}".lower()
    if any(kw in text for kw in ("short sell", "short-sell", " sell ", "sell signal")):
        return "sell"
    return "buy"


def _format_provider_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _infer_lane(source: Optional[IncomeSource]) -> Optional[str]:
    if not source:
        return None
    if source.notes and "Lane:" in source.notes:
        try:
            lane_value = source.notes.split("Lane:", 1)[1].splitlines()[0].strip()
            return lane_value or source.origin_module
        except Exception:
            return source.origin_module
    return source.origin_module


def _packet_payload(packet: ActionPacket, session: Session) -> dict:
    allocation = _get_allocation(packet.source_id, session)
    source = _get_source(packet.source_id, session)
    strategy = _get_strategy(packet.source_id, session)
    provider_order = session.exec(
        select(ProviderExecution)
        .where(ProviderExecution.packet_id == packet.id)
        .order_by(ProviderExecution.created_at.desc())
    ).first()
    return {
        "packet_id": packet.id,
        "source_id": packet.source_id,
        "opportunity_summary": packet.opportunity_summary,
        "status": packet.status,
        "execution_state": packet.execution_state,
        "priority_band": packet.priority_band,
        "estimated_return": packet.estimated_return,
        "budget_recommendation": packet.budget_recommendation,
        "execution_started_at": packet.execution_started_at.isoformat() if packet.execution_started_at else None,
        "execution_completed_at": packet.execution_completed_at.isoformat() if packet.execution_completed_at else None,
        "execution_failed_at": packet.execution_failed_at.isoformat() if packet.execution_failed_at else None,
        "provider_order": (
            {
                "provider": provider_order.provider,
                "mode": provider_order.provider_mode,
                "external_order_id": provider_order.external_order_id,
                "symbol": provider_order.symbol,
                "order_side": provider_order.order_side,
                "qty": provider_order.qty,
                "notional": provider_order.notional,
                "execution_status": provider_order.execution_status,
                "submitted_at": provider_order.submitted_at.isoformat() if provider_order.submitted_at else None,
            }
            if provider_order
            else None
        ),
        "allocation": (
            {
                "allocation_id": allocation.id,
                "amount_allocated": allocation.amount_allocated,
                "status": allocation.status,
            }
            if allocation
            else None
        ),
        "strategy": (
            {
                "strategy_id": strategy.strategy_id,
                "status": strategy.status,
            }
            if strategy
            else None
        ),
        "lane": _infer_lane(source),
        "category": source.category if source else None,
    }
