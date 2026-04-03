import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from sqlmodel import Field, SQLModel


class EventType(str, Enum):
    ingested = "ingested"
    scored = "scored"
    state_change = "state_change"
    budget_linked = "budget_linked"
    alert_raised = "alert_raised"
    packet_generated = "packet_generated"
    advisor_input = "advisor_input"
    strategy_activated = "strategy_activated"
    executed = "executed"
    outcome_logged = "outcome_logged"
    score_updated = "score_updated"
    performance_updated = "performance_updated"
    closed = "closed"
    error = "error"


class OpportunityEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    event_type: str
    old_state: Optional[str] = None
    new_state: Optional[str] = None
    summary: str
    metadata_json: Optional[str] = None   # JSON-encoded dict for arbitrary context
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_metadata(self, data: dict[str, Any]) -> None:
        self.metadata_json = json.dumps(data)

    def get_metadata(self) -> dict[str, Any]:
        if not self.metadata_json:
            return {}
        return json.loads(self.metadata_json)
