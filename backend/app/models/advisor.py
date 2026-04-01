from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import Field, SQLModel


class AdvisorName(str, Enum):
    venice = "venice"
    deepseek = "deepseek"
    grok = "grok"
    other = "other"


class AdvisorRecommendation(str, Enum):
    pursue = "pursue"
    park = "park"
    reject = "reject"
    escalate = "escalate"
    monitor = "monitor"


class AdvisorInput(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    advisor_name: str                    # AdvisorName value
    recommendation: str                  # AdvisorRecommendation value
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reasoning: str
    raw_response_json: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
