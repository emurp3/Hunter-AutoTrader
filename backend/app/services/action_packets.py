"""
Action packet service - generate and manage commander-ready action packets.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.income_source import IncomeSource
from app.services import advisors as advisor_svc


def generate_packet(source_id: str, session: Session) -> ActionPacket:
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise ValueError(f"No income source found: {source_id}")

    advisor_summary = advisor_svc.format_summary(source_id, session)
    consensus = advisor_svc.get_consensus(source_id, session)

    budget_rec = None
    try:
        from app.services.budget import recommend_allocation

        rec = recommend_allocation(source_id, session)
        budget_rec = rec.get("recommended_allocation")
    except Exception:
        if source.estimated_profit:
            budget_rec = round(source.estimated_profit * 0.20, 2)

    risk_parts = []
    if source.confidence is not None and source.confidence < 0.5:
        risk_parts.append(f"low confidence ({source.confidence:.0%})")
    if consensus in (None,):
        risk_parts.append("no advisor consensus")
    risk_notes = "; ".join(risk_parts) if risk_parts else None

    next_actions: list[str] = []
    if source.next_action:
        next_actions.append(source.next_action)
    if consensus == "pursue":
        next_actions.append("Advisors recommend pursue - allocate budget and execute")
    elif consensus == "park":
        next_actions.append("Advisors recommend park - monitor and revisit")
    elif consensus == "reject":
        next_actions.append("Advisors recommend reject - close source")
    elif consensus == "escalate":
        next_actions.append("Escalated for manual commander review")

    existing = get_packet(source_id, session)
    if existing and existing.status in (PacketStatus.draft, PacketStatus.ready):
        packet = existing
        packet.updated_at = datetime.now(timezone.utc)
    else:
        packet = ActionPacket(source_id=source_id)

    packet.opportunity_summary = source.description
    packet.score = source.score
    packet.priority_band = source.priority_band
    packet.estimated_return = source.estimated_profit
    packet.budget_recommendation = budget_rec
    packet.risk_notes = risk_notes
    packet.advisor_summary = advisor_summary
    packet.evidence = source.notes
    packet.status = PacketStatus.ready
    packet.execution_state = packet.execution_state or ExecutionState.planned
    packet.set_next_actions(next_actions)

    session.add(packet)
    session.commit()
    session.refresh(packet)
    return packet


def get_packet(source_id: str, session: Session) -> Optional[ActionPacket]:
    stmt = (
        select(ActionPacket)
        .where(ActionPacket.source_id == source_id)
        .order_by(ActionPacket.created_at.desc())
    )
    return session.exec(stmt).first()


def list_packets(session: Session, status: Optional[str] = None, limit: int = 100) -> list[ActionPacket]:
    stmt = select(ActionPacket).order_by(ActionPacket.created_at.desc()).limit(limit)
    if status:
        stmt = (
            select(ActionPacket)
            .where(ActionPacket.status == status)
            .order_by(ActionPacket.created_at.desc())
            .limit(limit)
        )
    return list(session.exec(stmt).all())


def promote_packet(packet_id: int, new_status: str, session: Session) -> Optional[ActionPacket]:
    packet = session.get(ActionPacket, packet_id)
    if not packet:
        return None
    packet.status = new_status
    packet.updated_at = datetime.now(timezone.utc)
    session.add(packet)
    session.commit()
    session.refresh(packet)
    return packet
