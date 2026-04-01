from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import Field, SQLModel


class AlertType(str, Enum):
    elite_opportunity = "elite_opportunity"
    budget_low = "budget_low"
    review_required = "review_required"
    underperforming = "underperforming"
    stale = "stale"
    source_failure = "source_failure"
    advisor_disagreement = "advisor_disagreement"
    strategy_shortfall = "strategy_shortfall"
    source_discovery_shortfall = "source_discovery_shortfall"
    strategy_stale = "strategy_stale"
    execution_completed = "execution_completed"
    execution_failed = "execution_failed"
    high_performing_lane = "high_performing_lane"
    underperforming_lane = "underperforming_lane"
    repeated_failure_pattern = "repeated_failure_pattern"


class AlertPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Alert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    alert_type: str
    priority: str = Field(default=AlertPriority.medium)
    title: str
    body: str
    source_id: Optional[str] = Field(default=None, index=True)
    acknowledged: bool = Field(default=False)
    acknowledged_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
