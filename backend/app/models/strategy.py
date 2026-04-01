from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import Field, SQLModel


class StrategyStatus(str, Enum):
    candidate = "candidate"
    activated = "activated"
    active = "active"
    underperforming = "underperforming"
    completed = "completed"
    failed = "failed"
    archived = "archived"


class Strategy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    strategy_id: str = Field(index=True, unique=True)
    strategy_name: str
    linked_opportunity_source_id: Optional[str] = Field(default=None, index=True)
    category: str
    date_activated: date
    status: str = Field(default=StrategyStatus.candidate)
    budget_assigned: Optional[float] = Field(default=None, ge=0)
    expected_return: Optional[float] = Field(default=None, ge=0)
    actual_return: Optional[float] = None
    owner: Optional[str] = None
    execution_path: Optional[str] = None       # description of how this gets executed
    evidence_of_activity: Optional[str] = None
    reason_for_continuation_or_termination: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StrategyCreate(SQLModel):
    strategy_name: str
    linked_opportunity_source_id: Optional[str] = None
    category: str
    expected_return: Optional[float] = Field(default=None, ge=0)
    execution_path: Optional[str] = None
    owner: Optional[str] = None


class StrategyUpdate(SQLModel):
    strategy_name: Optional[str] = None
    category: Optional[str] = None
    status: Optional[StrategyStatus] = None
    budget_assigned: Optional[float] = Field(default=None, ge=0)
    actual_return: Optional[float] = None
    evidence_of_activity: Optional[str] = None
    reason_for_continuation_or_termination: Optional[str] = None
    execution_path: Optional[str] = None
    owner: Optional[str] = None
