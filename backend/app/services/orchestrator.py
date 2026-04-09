"""
Opportunity orchestrator — drives each income source through the full lifecycle.

process_new_opportunity()   — score, classify, alert, packet (run post-ingest)
advance_opportunity_state() — transition source to next logical state
build_action_plan()         — generate or refresh action packet
should_escalate()           — determine if source needs commander attention
queue_commander_alert()     — raise a priority alert to the commander
"""

from sqlmodel import Session

from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.models.event import EventType
from app.models.alert import AlertType, AlertPriority
from app.services.scoring import score_opportunity
from app.services import events as event_svc
from app.services import alerts as alert_svc
from app.services import action_packets as packet_svc
import logging as _log
_logger = _log.getLogger(__name__)


def process_new_opportunity(source: IncomeSource, session: Session) -> dict:
    """
    Full post-ingest pipeline for a single opportunity:
      1. Score
      2. Persist score fields
      3. Advance state to 'scored'
      4. Raise elite alert if warranted
      5. Generate action packet
      6. Log audit event
    Returns a summary dict.
    """
    result = score_opportunity(source, session)

    old_state = source.status
    source.score = result.score
    source.priority_band = result.priority_band
    source.score_rationale = result.rationale
    source.status = SourceStatus.scored

    session.add(source)
    session.commit()
    session.refresh(source)

    # Audit
    event_svc.log_event(
        source.source_id,
        EventType.scored,
        session,
        old_state=old_state,
        new_state=SourceStatus.scored,
        summary=result.rationale,
        metadata={"score": result.score, "band": result.priority_band},
    )

    alerts_raised = []

    # Elite alert
    if result.priority_band == PriorityBand.elite:
        alert = alert_svc.raise_elite_opportunity_alert(
            source.source_id, result.score, source.description, session
        )
        alerts_raised.append(alert.id)
        event_svc.log_event(
            source.source_id,
            EventType.alert_raised,
            session,
            summary=f"Elite alert raised (id={alert.id})",
        )

    # Review-ready promotion for high+elite + auto-create linked strategy candidate
    if result.priority_band in (PriorityBand.elite, PriorityBand.high):
        advance_opportunity_state(source, SourceStatus.review_ready, session)
        try:
            from app.services.strategies import create_strategy_from_opportunity
            create_strategy_from_opportunity(source.source_id, session)
        except Exception:
            pass  # Strategy creation failure must not block the pipeline

    # Auto-allocate budget for elite/high sources
    if result.priority_band in (PriorityBand.elite, PriorityBand.high):
        try:
            from app.services.budget import auto_allocate_for_source
            alloc_result = auto_allocate_for_source(source.source_id, session)
            if alloc_result and not alloc_result.get("skipped"):
                event_svc.log_event(
                    source.source_id,
                    EventType.budget_linked,
                    session,
                    summary=f"Auto-allocated ${alloc_result['amount']:.2f} (approval_required={alloc_result['approval_required']})",
                )
        except Exception as _exc:  # noqa: BLE001
            _logger.error("Budget step failed: %s", _exc, exc_info=True)

    # Action packet
    packet = packet_svc.generate_packet(source.source_id, session)
    event_svc.log_event(
        source.source_id,
        EventType.packet_generated,
        session,
        summary=f"Action packet generated (id={packet.id})",
    )

    # Decision engine — route to action state + execution path
    decision_id = None
    try:
        from app.services import decision as decision_svc
        decision = decision_svc.decide(source, session)
        decision_id = decision.id
    except Exception as _exc:  # noqa: BLE001
        _logger.error("Decision step failed: %s", _exc, exc_info=True)

    # Auto-dispatch: high/elite always; also medium-band creation_lane opportunities
    task_id = None
    _should_dispatch = result.priority_band in (PriorityBand.elite, PriorityBand.high, PriorityBand.medium)
    if _should_dispatch:
        try:
            from app.services.tasks import auto_dispatch_for_source
            task = auto_dispatch_for_source(source.source_id, session)
            if task:
                task_id = task.task_id
        except Exception as _exc:  # noqa: BLE001
            _logger.error("Dispatch step failed: %s", _exc, exc_info=True)

    # Auto-trade: execution_ready trading decisions fire Alpaca orders immediately
    trade_placed = False
    if decision_id:
        try:
            from app.services import decision as decision_svc
            from app.services.execution import auto_place_trade_for_source
            dec = decision_svc.get_decision(source.source_id, session)
            if dec and dec.execution_ready and dec.execution_path == "trading":
                trade_result = auto_place_trade_for_source(source.source_id, session)
                trade_placed = trade_result is not None
        except Exception as _exc:  # noqa: BLE001
            _logger.error("Trade placement step failed: %s", _exc, exc_info=True)

    return {
        "source_id": source.source_id,
        "score": result.score,
        "priority_band": result.priority_band,
        "alerts_raised": alerts_raised,
        "packet_id": packet.id,
        "decision_id": decision_id,
        "task_id": task_id,
        "trade_placed": trade_placed,
    }


def advance_opportunity_state(
    source: IncomeSource,
    new_status: str,
    session: Session,
    *,
    reason: str = "",
) -> IncomeSource:
    old_state = source.status
    source.status = new_status
    session.add(source)
    session.commit()
    session.refresh(source)

    event_svc.log_event(
        source.source_id,
        EventType.state_change,
        session,
        old_state=old_state,
        new_state=new_status,
        summary=reason or f"State advanced: {old_state} → {new_status}",
    )
    return source


def build_action_plan(source_id: str, session: Session):
    """Regenerate action packet for a source (call after advisor inputs arrive)."""
    from sqlmodel import select
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise ValueError(f"No source found: {source_id}")
    packet = packet_svc.generate_packet(source_id, session)
    event_svc.log_event(
        source_id,
        EventType.packet_generated,
        session,
        summary=f"Action packet refreshed (id={packet.id})",
    )
    return packet


def should_escalate(source: IncomeSource, session: Session) -> bool:
    """Return True if the opportunity needs manual commander review."""
    from app.services.advisors import get_consensus, get_disagreements
    if source.priority_band == PriorityBand.elite:
        return True
    disagreements = get_disagreements(session)
    if source.source_id in disagreements:
        return True
    if source.confidence is not None and source.confidence < 0.3:
        return True
    return False


def queue_commander_alert(
    source: IncomeSource,
    session: Session,
    *,
    reason: str = "Manual review required",
) -> None:
    alert_svc.raise_alert(
        alert_type=AlertType.review_required,
        title=f"Commander Review Required — {source.source_id}",
        body=f"{source.description} | band={source.priority_band} score={source.score} | {reason}",
        session=session,
        priority=AlertPriority.high,
        source_id=source.source_id,
    )
    event_svc.log_event(
        source.source_id,
        EventType.alert_raised,
        session,
        summary=f"Commander alert queued: {reason}",
    )
