"""
OpportunityDecision — the action layer between scoring and execution.

One record per IncomeSource. Captures:
  - action_state: what Hunter has decided to do with this opportunity
  - execution_path: which action channel to use
  - approval gate: who or what is blocking execution
  - action_payload: concrete next-step data (structured JSON)
  - capital_recommendation: how much bankroll to commit
  - feedback_adjustment: score delta from historical performance
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlmodel import Field, SQLModel


class ActionState(str, Enum):
    ignore = "ignore"              # below minimum threshold — no action
    watch = "watch"                # marginal — monitor for improvement
    review_ready = "review_ready"  # qualified — needs commander review
    ready_to_act = "ready_to_act"  # approved for action, awaiting execution
    auto_execute = "auto_execute"  # meets all criteria for autonomous execution


class ExecutionPath(str, Enum):
    outreach = "outreach"                      # job/contract application
    arbitrage = "arbitrage"                    # buy-low sell-high deal
    local_pitch = "local_pitch"               # small business service offer
    automation_proposal = "automation_proposal"  # code/automation consulting
    affiliate_content = "affiliate_content"   # content + affiliate monetization
    trading = "trading"                        # Alpaca market execution
    advisor_review = "advisor_review"          # needs advisor consensus first
    none = "none"                              # no path assigned


class OpportunityDecision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True, unique=True)

    # Decision
    action_state: str = Field(default=ActionState.watch)
    execution_path: str = Field(default=ExecutionPath.none)
    score_at_decision: Optional[float] = None
    confidence_at_decision: Optional[float] = None
    feedback_adjustment: float = Field(default=0.0)  # from performance history

    # Approval gate
    approval_required: bool = Field(default=False)
    approval_reason: Optional[str] = None
    execution_ready: bool = Field(default=False)
    blocked_by: Optional[str] = None      # "approval" | "low_confidence" | "no_capital" | None

    # Capital
    capital_recommendation: Optional[float] = None  # suggested allocation amount

    # Concrete action payload (JSON)
    action_payload_json: Optional[str] = None

    # Review
    reviewed_at: Optional[datetime] = None
    reviewer_note: Optional[str] = None

    # Timestamps
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_action_payload(self, payload: dict[str, Any]) -> None:
        self.action_payload_json = json.dumps(payload)

    def get_action_payload(self) -> dict[str, Any]:
        if not self.action_payload_json:
            return {}
        return json.loads(self.action_payload_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "action_state": self.action_state,
            "execution_path": self.execution_path,
            "score_at_decision": self.score_at_decision,
            "confidence_at_decision": self.confidence_at_decision,
            "feedback_adjustment": self.feedback_adjustment,
            "approval_required": self.approval_required,
            "approval_reason": self.approval_reason,
            "execution_ready": self.execution_ready,
            "blocked_by": self.blocked_by,
            "capital_recommendation": self.capital_recommendation,
            "action_payload": self.get_action_payload(),
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewer_note": self.reviewer_note,
            "decided_at": self.decided_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
