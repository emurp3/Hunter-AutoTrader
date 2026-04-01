"""
Audit trail service — log every state transition and operation.

log_event(source_id, event_type, session, *, old_state, new_state, summary, metadata)
"""

from typing import Any, Optional

from sqlmodel import Session

from app.models.event import EventType, OpportunityEvent


def log_event(
    source_id: str,
    event_type: str,
    session: Session,
    *,
    old_state: Optional[str] = None,
    new_state: Optional[str] = None,
    summary: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> OpportunityEvent:
    event = OpportunityEvent(
        source_id=source_id,
        event_type=event_type,
        old_state=old_state,
        new_state=new_state,
        summary=summary,
    )
    if metadata:
        event.set_metadata(metadata)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def get_events(source_id: str, session: Session) -> list[OpportunityEvent]:
    from sqlmodel import select
    stmt = select(OpportunityEvent).where(OpportunityEvent.source_id == source_id).order_by(OpportunityEvent.created_at)
    return list(session.exec(stmt).all())
