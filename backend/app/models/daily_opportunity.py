"""
Daily opportunity and advisor scoring models.

DailyOpportunity — one advisor-generated profit opportunity per day
AdvisorWeeklyScore — rolling weekly performance tracker per advisor
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class OpportunityLane(str, Enum):
    trading = "trading"
    marketplace = "marketplace"
    service = "service"
    digital = "digital"


class OpportunityStatus(str, Enum):
    pending = "pending"       # generated, not yet acted on
    dispatched = "dispatched" # handed to execution pipeline
    succeeded = "succeeded"   # confirmed profit
    failed = "failed"         # attempted, no profit
    skipped = "skipped"       # not acted on that day


class DailyOpportunity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    opp_date: date = Field(index=True)                    # calendar date this opportunity owns
    assigned_advisor: str                                  # the scheduled owner (per rotation)
    actual_advisor: str                                    # who actually generated it (may differ if fallback)
    title: str
    lane: str                                              # OpportunityLane value
    rationale: str                                         # why it should produce profit today
    required_action: str
    expected_profit: float = Field(default=0.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    handoff_path: Optional[str] = Field(default=None)     # execution_path or handoff instructions
    status: str = Field(default=OpportunityStatus.pending)
    actual_profit: Optional[float] = Field(default=None)  # filled in when outcome is known
    outcome_notes: Optional[str] = Field(default=None)
    raw_response_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default=None)


class AdvisorWeeklyScore(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    week_start: date = Field(index=True)
    advisor_name: str = Field(index=True)
    opportunities_generated: int = Field(default=0)
    opportunities_dispatched: int = Field(default=0)
    opportunities_succeeded: int = Field(default=0)
    total_actual_profit: float = Field(default=0.0)
    is_winner: bool = Field(default=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
