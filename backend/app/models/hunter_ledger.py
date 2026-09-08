"""
Hunter Execution Ledger — models for the Claude Hunter Implementation
Addendum (2026-09-08).

This module implements the controlling execution specification from
`Hunter_Opportunity_Queue_2026-09-08.md` as an enforceable data model:

  - BaselineManifestEntry — the frozen, numbered manifest of source posts
    (baseline + incremental queue). Indices are never renumbered.
  - CanonicalOpportunity  — one deduplicated opportunity with full rescue
    schema, current disposition, and cash accounting.
  - RescueAttempt         — one pursued alternative for a candidate before
    any permanent rejection is allowed.
  - ExecutionRecord       — the only table that may increment the daily
    execution quota. Requires an external receipt at creation time.
  - ReplacementChain      — tracks the parallel candidate opened whenever
    an opportunity is blocked or parked on a Commander checkpoint.
  - FormalGate            — the named daily gate evaluations (e.g.
    HUNTER-OPP-2026-09-08-UCP-01), separate from execution credit.

Statuses that count as zero executions (per addendum): PENDING_COMMANDER,
WATCHLIST, REJECTED, DUPLICATE, BLOCKED, EXPIRED, INAPPLICABLE,
DEFERRED_FOR_HIGHER_VALUE, SCREENED_ONLY. Only EXECUTED counts, and only
when backed by a row in ExecutionRecord with a non-empty receipt.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Disposition(str, Enum):
    """Every status a CanonicalOpportunity may carry. Only EXECUTED counts
    toward the daily/weekly/cycle quota — every other value is zero."""

    executed = "EXECUTED"
    pending_research = "PENDING_RESEARCH"          # created, Hunter has not yet run research on it
    pending_commander = "PENDING_COMMANDER"
    watchlist = "WATCHLIST"
    rejected = "REJECTED"                          # REJECTED implies "after rescue" — enforced below,
                                                    # never settable without a documented rescue history
    duplicate = "DUPLICATE"
    blocked = "BLOCKED"                            # a real, substantive finding: route currently closed/
                                                    # unavailable, reachable and evaluated, not a permanent call
    blocked_infrastructure = "BLOCKED_INFRASTRUCTURE"  # Hunter's research ran but could not reach the
                                                        # network at all — says nothing about the opportunity
    blocked_capability = "BLOCKED_CAPABILITY"      # Hunter has no research capability wired for this
                                                    # candidate yet (no connector, no LLM key, a provider bug)
                                                    # — an engineering gap, not a judgment on the opportunity
    expired = "EXPIRED"
    inapplicable = "INAPPLICABLE"
    deferred_for_higher_value = "DEFERRED_FOR_HIGHER_VALUE"
    screened_only = "SCREENED_ONLY"


# Dispositions that are final for reconciliation purposes but never carry
# execution credit.
ZERO_EXECUTION_DISPOSITIONS = {
    Disposition.pending_research,
    Disposition.pending_commander,
    Disposition.watchlist,
    Disposition.rejected,
    Disposition.duplicate,
    Disposition.blocked,
    Disposition.blocked_infrastructure,
    Disposition.blocked_capability,
    Disposition.expired,
    Disposition.inapplicable,
    Disposition.deferred_for_higher_value,
    Disposition.screened_only,
}

# Dispositions that represent "Hunter could not complete research" rather
# than a substantive finding about the opportunity itself. An opportunity
# in one of these states is still alive — it must stay eligible for the
# rescue/replacement mechanism and must never be reported as if Hunter
# judged it bad.
NON_SUBSTANTIVE_DISPOSITIONS = {
    Disposition.pending_research,
    Disposition.blocked_infrastructure,
    Disposition.blocked_capability,
}

# A candidate may only be permanently rejected, or marked substantively
# blocked, after a rescue path has actually been pursued and evidenced.
# The infra/capability blocks are included too: Hunter's research engine
# always logs a rescue attempt describing what it tried before landing
# here, so this stays a real invariant rather than a formality.
DISPOSITIONS_REQUIRING_RESCUE_HISTORY = {
    Disposition.rejected,
    Disposition.blocked,
    Disposition.blocked_infrastructure,
    Disposition.blocked_capability,
}

# Dispositions that must always open (or already have open) a parallel
# replacement chain so the operating day isn't consumed by one blocker.
DISPOSITIONS_REQUIRING_REPLACEMENT = {
    Disposition.pending_commander,
    Disposition.blocked,
    Disposition.blocked_infrastructure,
    Disposition.blocked_capability,
}


class RescueType(str, Enum):
    official_successor = "official_successor"
    alternate_channel = "alternate_channel"
    lawful_current_version = "lawful_current_version"
    obtainable_prerequisites = "obtainable_prerequisites"
    authorized_partner = "authorized_partner"
    different_jurisdiction_or_segment = "different_jurisdiction_or_segment"
    approved_clarification_contact = "approved_clarification_contact"
    time_triggered_activation = "time_triggered_activation"


class RescueResult(str, Enum):
    found = "found"
    not_found = "not_found"
    pending = "pending"


class GateVerdict(str, Enum):
    pass_ = "PASS"
    fail = "FAIL"
    pending_evidence = "PENDING_EVIDENCE"


# ── Baseline manifest ─────────────────────────────────────────────────────


class BaselineManifestEntry(SQLModel, table=True):
    """One numbered slot in the frozen source manifest. `baseline_index`
    is immutable once created — posts that disappear or get superseded
    are marked via `screening_status` / `content_available`, never
    renumbered or deleted."""

    id: Optional[int] = Field(default=None, primary_key=True)
    baseline_index: int = Field(index=True, unique=True)
    source_profile: str = Field(default="systemcracker_1")
    canonical_source_url: str
    captured_at: datetime = Field(default_factory=_utcnow)
    source_title_or_claim: str
    source_evidence_reference: Optional[str] = Field(default=None)
    content_available: bool = Field(default=True)
    screening_status: str = Field(default="unscreened")
    canonical_opportunity_id: Optional[str] = Field(default=None, index=True)
    duplicate_of: Optional[int] = Field(default=None)
    incremental_after_baseline: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)


# ── Canonical opportunity + rescue schema ─────────────────────────────────


class CanonicalOpportunity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_opportunity_id: str = Field(index=True, unique=True)

    # Lane + provenance
    lane: str
    source_post_refs: Optional[str] = Field(default=None)  # pipe-delimited baseline_index / ids
    factual_mechanism: str
    current_lawful_implementation: Optional[str] = Field(default=None)
    source_provenance: str
    freshness_date: date

    # Eligibility
    eligibility: Optional[str] = Field(default=None)
    jurisdiction: Optional[str] = Field(default=None)

    # Value estimate
    estimated_value_low: Optional[float] = Field(default=None)
    estimated_value_high: Optional[float] = Field(default=None)
    probability_adjusted_pending_value: float = Field(default=0.0)

    # Burden / risk
    documentation_burden: Optional[str] = Field(default=None)
    contactability: Optional[str] = Field(default=None)
    compliance_risk: Optional[str] = Field(default=None)
    legal_risk: Optional[str] = Field(default=None)
    privacy_risk: Optional[str] = Field(default=None)
    financial_risk: Optional[str] = Field(default=None)
    platform_risk: Optional[str] = Field(default=None)

    # Cost / controls
    costs: float = Field(default=0.0)
    spending_risk_controls: Optional[str] = Field(default=None)

    # Score + disposition
    score: Optional[float] = Field(default=None, index=True)
    disposition: str = Field(default=Disposition.pending_research, index=True)

    # Hunter's own research findings — appended to by the research engine
    # (app/services/research/), never hand-typed by Claude as if Hunter
    # produced it.
    evidence_log: Optional[str] = Field(default=None)

    # Checkpoints / ownership
    required_commander_checkpoints: Optional[str] = Field(default=None)
    next_action: Optional[str] = Field(default=None)
    owner: str = Field(default="Hunter")
    due_date: Optional[date] = Field(default=None)

    # Execution endpoint / receipt (mirrors the linked ExecutionRecord)
    execution_endpoint: Optional[str] = Field(default=None)
    external_receipt_ref: Optional[str] = Field(default=None)

    # Realized cash — ONLY ever written by the settle_cash() accounting
    # function, never by scoring/research/projection code paths.
    realized_gross_cash: Optional[float] = Field(default=None)
    settled_expenses: Optional[float] = Field(default=None)
    net_realized_cash: Optional[float] = Field(default=None)

    duplicate_of_canonical_opportunity_id: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RescueAttempt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_opportunity_id: str = Field(index=True)
    rescue_type: str
    description: str
    result: str = Field(default=RescueResult.pending)
    evidence_reference: Optional[str] = Field(default=None)
    attempted_at: datetime = Field(default_factory=_utcnow)


# ── Execution accounting ──────────────────────────────────────────────────


class ExecutionRecord(SQLModel, table=True):
    """The only table permitted to increment the daily/weekly/cycle
    execution quota. Creation requires a non-empty external_endpoint and
    receipt_reference — accounting rejects anything missing either."""

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_opportunity_id: str = Field(index=True)
    source: str
    action_description: str
    actions_taken: str
    external_endpoint: str
    receipt_reference: str = Field(index=True, unique=True)
    timestamp: datetime = Field(default_factory=_utcnow, index=True)

    money_spent_committed: float = Field(default=0.0)
    expected_lawful_return: float = Field(default=0.0)
    expected_time_to_cash: Optional[str] = Field(default=None)
    follow_up: Optional[str] = Field(default=None)
    owner: str = Field(default="Hunter")
    due_date: Optional[date] = Field(default=None)

    # Realized cash — null until settle_cash() is explicitly called with
    # an evidence_reference. Never auto-populated from expected_lawful_return
    # or any other projected/hypothetical figure.
    realized_gross_cash: Optional[float] = Field(default=None)
    settled_expenses: Optional[float] = Field(default=None)
    net_realized_cash: Optional[float] = Field(default=None)
    settled_evidence_reference: Optional[str] = Field(default=None)
    settled_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)


class ReplacementChain(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    blocked_canonical_opportunity_id: str = Field(index=True)
    replacement_canonical_opportunity_id: Optional[str] = Field(default=None, index=True)
    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = Field(default=None)
    result: Optional[str] = Field(default=None)


class FormalGate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    gate_id: str = Field(index=True, unique=True)
    title: str
    canonical_opportunity_id: Optional[str] = Field(default=None, index=True)
    verdict: str = Field(default=GateVerdict.pending_evidence)
    evidence_summary: Optional[str] = Field(default=None)
    receipts: Optional[str] = Field(default=None)
    counts_toward_quota: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
