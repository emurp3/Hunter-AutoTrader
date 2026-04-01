import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from sqlmodel import Field, SQLModel


class PacketStatus(str, Enum):
    draft = "draft"
    ready = "ready"
    acknowledged = "acknowledged"
    executed = "executed"


class ExecutionState(str, Enum):
    planned = "planned"
    active = "active"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class ActionPacket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    opportunity_summary: str
    score: Optional[float] = None
    priority_band: Optional[str] = None
    estimated_return: Optional[float] = None
    budget_recommendation: Optional[float] = None
    risk_notes: Optional[str] = None
    advisor_summary: Optional[str] = None
    next_actions_json: Optional[str] = None    # JSON list of strings
    evidence: Optional[str] = None
    status: str = Field(default=PacketStatus.draft)
    execution_state: str = Field(default=ExecutionState.planned, index=True)
    execution_started_at: Optional[datetime] = None
    execution_updated_at: Optional[datetime] = None
    execution_completed_at: Optional[datetime] = None
    execution_failed_at: Optional[datetime] = None
    execution_canceled_at: Optional[datetime] = None
    execution_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_next_actions(self, actions: list[str]) -> None:
        self.next_actions_json = json.dumps(actions)

    def get_next_actions(self) -> list[str]:
        if not self.next_actions_json:
            return []
        return json.loads(self.next_actions_json)
