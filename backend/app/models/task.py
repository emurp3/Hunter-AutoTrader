"""
Task and TaskAttempt models for Hunter's execution dispatch system.

Hunter creates Tasks for work that requires interface-based execution.
Tasks are claimed by the assistant worker, executed via the appropriate
skill or fallback engine, and outcomes reported back. Hunter closes the
loop into its own ledger, strategy, and budget systems.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class TaskStatus(str, Enum):
    created = "created"
    dispatched = "dispatched"
    executing = "executing"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    escalated = "escalated"
    rerouting = "rerouting"
    awaiting_human_verification = "awaiting_human_verification"
    resuming = "resuming"


class ExecutionEngine(str, Enum):
    playwright = "playwright"
    claude_cu = "claude_cu"
    direct_api = "direct_api"
    manual_none = "manual_none"


class EscalationType(str, Enum):
    credentials_required = "credentials_required"
    platform_lockout = "platform_lockout"
    commander_boundary = "commander_boundary"
    unrecoverable_failure = "unrecoverable_failure"
    commander_flag = "commander_flag"
    # A required input (e.g. contact info) was genuinely absent after a
    # real investigation — distinct from unrecoverable_failure so
    # dispatch_task can refuse to blindly re-run the same task against
    # the same unchanged input (see dispatch_task's idempotency check).
    contact_unavailable = "contact_unavailable"
    external_outcome_uncertain = "external_outcome_uncertain"


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    # Identity
    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        index=True,
        unique=True,
    )
    idempotency_key: str = Field(default="", index=True)

    # Classification
    task_type: str = Field(index=True)          # e.g. "marketplace_listing"
    source_type: str = Field(default="income_source")
    source_id: Optional[str] = Field(default=None, index=True)
    packet_id: Optional[int] = Field(default=None)
    strategy_id: Optional[str] = Field(default=None)
    priority: int = Field(default=5)            # 1=low 5=medium 10=high 15=elite

    # Spec — stored as JSON strings (SQLite compatibility)
    spec_version: str = Field(default="1.0")
    spec_payload: str = Field(default="{}")     # full structured task instructions
    success_criteria: str = Field(default="{}")
    escalate_rules: str = Field(default="{}")
    must_escalate: bool = Field(default=False)

    # Routing
    preferred_engine: ExecutionEngine = Field(default=ExecutionEngine.playwright)
    allowed_engines: str = Field(default='["playwright","claude_cu"]')  # JSON list

    # Execution state
    status: TaskStatus = Field(default=TaskStatus.dispatched, index=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    verification_attempts: int = Field(default=0)
    verification_state: Optional[str] = Field(default=None, index=True)
    resume_checkpoint_json: Optional[str] = Field(default=None)

    # Worker lease — atomic claiming and heartbeat
    worker_id: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None)
    last_heartbeat_at: Optional[datetime] = Field(default=None)

    # Outcome
    outcome: Optional[str] = Field(default=None)       # JSON string
    outcome_notes: Optional[str] = Field(default=None)

    # A durable receipt used to finish reporting after worker loss. By itself
    # this does not cover the send-to-record gap; outreach_intent_json does.
    pending_outcome_json: Optional[str] = Field(default=None)
    pending_outcome_recorded_at: Optional[datetime] = Field(default=None)
    # Committed before SMTP submission. Never cleared by terminal callbacks.
    outreach_intent_json: Optional[str] = Field(default=None)
    outreach_intent_at: Optional[datetime] = Field(default=None)

    # Escalation
    escalation_reason: Optional[str] = Field(default=None)
    escalation_type: Optional[EscalationType] = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dispatched_at: Optional[datetime] = Field(default=None)
    executing_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    failed_at: Optional[datetime] = Field(default=None)
    escalated_at: Optional[datetime] = Field(default=None)


class VerificationReceipt(SQLModel, table=True):
    """Durable audit record for passive and interactive access friction."""
    id: Optional[int] = Field(default=None, primary_key=True)
    objective_id: Optional[str] = Field(default=None, index=True)
    task_id: str = Field(index=True)
    target_site: str = Field(default="")
    url: str = Field(default="")
    challenge_type: str = Field(default="unknown")
    challenge_reality: str = Field(default="real")
    attempted_recovery_paths: str = Field(default="[]")
    current_state: str = Field(default="ACTIVE", index=True)
    required_human_action: Optional[str] = Field(default=None)
    resume_checkpoint: str = Field(default="{}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class TaskAttempt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)            # denormalized for fast lookup
    attempt_number: int
    engine: ExecutionEngine
    worker_id: Optional[str] = Field(default=None)
    status: str = Field(default="started")      # started | completed | failed | escalated

    # Artifact capture on failure
    screenshot_path: Optional[str] = Field(default=None)
    page_url: Optional[str] = Field(default=None)
    error_text: Optional[str] = Field(default=None)
    trace_reference: Optional[str] = Field(default=None)
    summary_reason: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)

    # Outcome
    outcome: Optional[str] = Field(default=None)  # JSON string
    is_escalation: bool = Field(default=False)
    escalation_type: Optional[EscalationType] = Field(default=None)

    # Timestamps
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(default=None)
