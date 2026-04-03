"""
Assistant handoff queue.

Hunter is the decision-maker. The assistant removes friction by surfacing
tasks that need human or assistant attention, classified by type.

Task types:
  follow_up_required      — source needs a Commander follow-up action
  packet_preparation      — action packet needs enrichment before execution
  environment_fix_needed  — a config/env issue blocks an execution path
  strategy_evidence_due   — strategy needs evidence_of_activity logged
  budget_approval_needed  — allocation flagged approval_required=True
  advisor_review_needed   — advisor disagreement requires Commander decision

The queue is in-memory. Entries auto-expire when acknowledged or resolved.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class HandoffType(str, Enum):
    follow_up_required = "follow_up_required"
    packet_preparation = "packet_preparation"
    environment_fix_needed = "environment_fix_needed"
    strategy_evidence_due = "strategy_evidence_due"
    budget_approval_needed = "budget_approval_needed"
    advisor_review_needed = "advisor_review_needed"


class HandoffItem:
    def __init__(
        self,
        *,
        task_type: HandoffType,
        title: str,
        detail: str,
        source_id: Optional[str] = None,
        packet_id: Optional[int] = None,
        strategy_id: Optional[str] = None,
        allocation_id: Optional[int] = None,
        priority: str = "medium",
    ):
        self.id = str(uuid.uuid4())[:8]
        self.task_type = task_type
        self.title = title
        self.detail = detail
        self.source_id = source_id
        self.packet_id = packet_id
        self.strategy_id = strategy_id
        self.allocation_id = allocation_id
        self.priority = priority
        self.created_at = datetime.now(timezone.utc)
        self.acknowledged = False
        self.acknowledged_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "title": self.title,
            "detail": self.detail,
            "source_id": self.source_id,
            "packet_id": self.packet_id,
            "strategy_id": self.strategy_id,
            "allocation_id": self.allocation_id,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
        }


# In-memory queue
_QUEUE: dict[str, HandoffItem] = {}


def enqueue(
    *,
    task_type: HandoffType,
    title: str,
    detail: str,
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    strategy_id: Optional[str] = None,
    allocation_id: Optional[int] = None,
    priority: str = "medium",
) -> HandoffItem:
    item = HandoffItem(
        task_type=task_type,
        title=title,
        detail=detail,
        source_id=source_id,
        packet_id=packet_id,
        strategy_id=strategy_id,
        allocation_id=allocation_id,
        priority=priority,
    )
    _QUEUE[item.id] = item
    return item


def get_queue(*, include_acknowledged: bool = False) -> list[dict]:
    items = [v for v in _QUEUE.values() if include_acknowledged or not v.acknowledged]
    items.sort(key=lambda x: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x.priority, 2), x.created_at))
    return [item.to_dict() for item in items]


def acknowledge(item_id: str) -> Optional[dict]:
    item = _QUEUE.get(item_id)
    if not item:
        return None
    item.acknowledged = True
    item.acknowledged_at = datetime.now(timezone.utc)
    return item.to_dict()


def dismiss(item_id: str) -> bool:
    return _QUEUE.pop(item_id, None) is not None


def queue_summary() -> dict:
    all_items = list(_QUEUE.values())
    pending = [i for i in all_items if not i.acknowledged]
    by_type: dict[str, int] = {}
    for item in pending:
        by_type[item.task_type] = by_type.get(item.task_type, 0) + 1
    return {
        "total": len(all_items),
        "pending": len(pending),
        "acknowledged": len(all_items) - len(pending),
        "by_type": by_type,
    }
