from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ExecutionOutcome(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    action_packet_id: int = Field(foreign_key="actionpacket.id", index=True)
    allocation_id: Optional[int] = Field(default=None, foreign_key="budgetallocation.id", index=True)
    source_id: str = Field(index=True)
    strategy_id: Optional[str] = Field(default=None, index=True)
    lane: Optional[str] = Field(default=None, index=True)
    category: Optional[str] = Field(default=None, index=True)
    execution_state: str = Field(index=True)
    allocated_amount: Optional[float] = None
    actual_return: Optional[float] = None
    time_to_completion_hours: Optional[float] = None
    success_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    notes: Optional[str] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
