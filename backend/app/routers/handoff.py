"""
Assistant handoff queue endpoints.

GET  /handoff/              — pending handoff tasks
POST /handoff/              — enqueue a new handoff task
POST /handoff/{id}/ack      — acknowledge a handoff item
DELETE /handoff/{id}        — dismiss a handoff item
GET  /handoff/summary       — queue counts by type
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.handoff import HandoffType, acknowledge, dismiss, enqueue, get_queue, queue_summary

router = APIRouter(prefix="/handoff", tags=["handoff"])


class HandoffCreate(BaseModel):
    task_type: HandoffType
    title: str
    detail: str
    source_id: Optional[str] = None
    packet_id: Optional[int] = None
    strategy_id: Optional[str] = None
    allocation_id: Optional[int] = None
    priority: str = "medium"


@router.get("/summary")
def get_summary():
    return queue_summary()


@router.get("/")
def list_queue(include_acknowledged: bool = False):
    return get_queue(include_acknowledged=include_acknowledged)


@router.post("/", status_code=201)
def create_handoff(payload: HandoffCreate):
    item = enqueue(
        task_type=payload.task_type,
        title=payload.title,
        detail=payload.detail,
        source_id=payload.source_id,
        packet_id=payload.packet_id,
        strategy_id=payload.strategy_id,
        allocation_id=payload.allocation_id,
        priority=payload.priority,
    )
    return item.to_dict()


@router.post("/{item_id}/ack")
def ack_handoff(item_id: str):
    result = acknowledge(item_id)
    if not result:
        raise HTTPException(status_code=404, detail="Handoff item not found")
    return result


@router.delete("/{item_id}")
def dismiss_handoff(item_id: str):
    if not dismiss(item_id):
        raise HTTPException(status_code=404, detail="Handoff item not found")
    return {"dismissed": item_id}
