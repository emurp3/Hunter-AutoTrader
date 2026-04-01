from datetime import date
from enum import Enum
from typing import Optional
from sqlmodel import Field, SQLModel


class SourceStatus(str, Enum):
    # ── Original states ───────────────────────────────────────────────────────
    new = "new"
    active = "active"
    parked = "parked"
    exhausted = "exhausted"
    rejected = "rejected"
    complete = "complete"
    # ── Operational lifecycle states (added for orchestration) ────────────────
    ingested = "ingested"          # pulled in from a field module
    scored = "scored"              # scoring + priority assigned
    prioritized = "prioritized"    # orchestrator has classified it
    review_ready = "review_ready"  # ready for commander review
    budgeted = "budgeted"          # budget allocation linked
    outcome_logged = "outcome_logged"  # real outcome recorded
    archived = "archived"          # closed without failure
    failed = "failed"              # pursued but did not deliver


class PriorityBand(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    elite = "elite"


class IncomeSourceBase(SQLModel):
    source_id: str = Field(index=True, unique=True)
    description: str
    estimated_profit: float = Field(ge=0)
    currency: str = Field(default="USD")
    status: SourceStatus = Field(default=SourceStatus.new)
    date_found: date
    next_action: Optional[str] = None
    notes: Optional[str] = None
    # ── Provenance / intake metadata ──────────────────────────────────────────
    origin_module: Optional[str] = Field(default=None, index=True)
    category: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class IncomeSource(IncomeSourceBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # ── Server-computed ───────────────────────────────────────────────────────
    score: Optional[float] = Field(default=None, index=True)
    priority_band: Optional[str] = Field(default=None, index=True)   # PriorityBand value
    score_rationale: Optional[str] = Field(default=None)             # human-readable rationale


class IncomeSourceCreate(IncomeSourceBase):
    pass


class IncomeSourceUpdate(SQLModel):
    description: Optional[str] = None
    estimated_profit: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    status: Optional[SourceStatus] = None
    next_action: Optional[str] = None
    notes: Optional[str] = None
    origin_module: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
