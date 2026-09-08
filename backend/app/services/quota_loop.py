"""
Quota-protection worker loop (Claude Hunter Implementation Addendum,
section 4).

    1. Pull the highest-scoring executable candidate.
    2. Perform minimum lawful/current/applicability preflight.
    3. Identify Commander checkpoints immediately.
    4. If controllable, execute to an external endpoint and store receipt.
    5. If blocked or Commander-controlled, persist the state and rescue
       chain, then pull a parallel candidate without abandoning original.
    6. Continue until five separate receipts exist or the cutoff hits.
    7. Declare daily PASS only for five valid executions/receipts, else FAIL.

`preflight_fn` and `executor_fn` are injection points. The default
executor never fabricates a receipt — it returns None, which routes the
candidate to BLOCKED (with a documented rescue attempt noting no
automated executor is wired for that lane) rather than inventing an
execution. A real browser/API-driven executor should be passed in by the
caller once one exists for a given lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional

from sqlmodel import Session, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition, RescueResult, RescueType
from app.services import execution_accounting as acct


@dataclass
class PreflightResult:
    lawful: bool
    current: bool
    applicable: bool
    commander_checkpoint: Optional[str] = None
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.lawful and self.current and self.applicable


PreflightFn = Callable[[CanonicalOpportunity], PreflightResult]
# Executor returns kwargs for execution_accounting.record_execution(), or
# None when it could not reach a real external endpoint right now.
ExecutorFn = Callable[[CanonicalOpportunity], Optional[dict]]


def default_preflight(opp: CanonicalOpportunity) -> PreflightResult:
    """Minimum structural preflight: surfaces any declared Commander
    checkpoint immediately. Does not itself verify legal/current/applicable
    facts — that verification belongs in the rescue engine / gate research
    for each specific opportunity."""
    return PreflightResult(
        lawful=True,
        current=True,
        applicable=True,
        commander_checkpoint=opp.required_commander_checkpoints or None,
        notes="structural preflight only — no automated legal/current/applicability verifier wired",
    )


def default_executor(_opp: CanonicalOpportunity) -> Optional[dict]:
    """No automated, receipt-producing executor is wired for any lane in
    this addendum yet. Real executions require a human/browser-driven
    session with Commander's login/approval in the loop."""
    return None


@dataclass
class LoopStep:
    canonical_opportunity_id: str
    outcome: str  # executed | pending_commander | blocked | inapplicable | no_candidate
    detail: str = ""


@dataclass
class LoopResult:
    day: date
    started_at: datetime
    finished_at: datetime
    steps: list[LoopStep] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)

    @property
    def execution_count(self) -> int:
        return len(self.receipts)

    @property
    def verdict(self) -> str:
        return "PASS" if self.execution_count >= acct.DAILY_EXECUTION_QUOTA else "FAIL"


def pull_highest_scoring_candidate(session: Session) -> Optional[CanonicalOpportunity]:
    """Highest-score candidate still open for execution today
    (SCREENED_ONLY or WATCHLIST — i.e. not yet decided either way)."""
    candidates = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.disposition.in_(
                [Disposition.screened_only.value, Disposition.watchlist.value]
            )
        )
    ).all()
    if not candidates:
        return None
    return max(candidates, key=lambda o: (o.score if o.score is not None else -1.0, o.canonical_opportunity_id))


def run_quota_protection_loop(
    session: Session,
    *,
    day: Optional[date] = None,
    preflight_fn: PreflightFn = default_preflight,
    executor_fn: ExecutorFn = default_executor,
    max_iterations: int = 50,
) -> LoopResult:
    d = day or datetime.now(timezone.utc).date()
    acct.assert_autonomous_operations_allowed(d)

    result = LoopResult(day=d, started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))

    iterations = 0
    while result.execution_count < acct.DAILY_EXECUTION_QUOTA and iterations < max_iterations:
        iterations += 1
        candidate = pull_highest_scoring_candidate(session)
        if not candidate:
            result.steps.append(LoopStep("", "no_candidate", "no executable candidates remain in SCREENED_ONLY/WATCHLIST"))
            break

        cid = candidate.canonical_opportunity_id
        pre = preflight_fn(candidate)

        if not pre.passed:
            acct.set_disposition(
                session, cid, Disposition.inapplicable,
                evidence=pre.notes or "failed lawful/current/applicability preflight",
            )
            result.steps.append(LoopStep(cid, "inapplicable", pre.notes))
            continue

        if pre.commander_checkpoint:
            acct.set_disposition(
                session, cid, Disposition.pending_commander,
                evidence=f"Commander checkpoint required: {pre.commander_checkpoint}",
            )
            result.steps.append(LoopStep(cid, "pending_commander", pre.commander_checkpoint))
            continue

        exec_kwargs = executor_fn(candidate)
        if not exec_kwargs:
            acct.record_rescue_attempt(
                session, cid, RescueType.alternate_channel.value,
                description="Automated quota loop found no wired executor for this lane today.",
                result=RescueResult.not_found,
                evidence_reference=f"lane={candidate.lane}; no automated executor wired as of {d.isoformat()}",
            )
            acct.set_disposition(
                session, cid, Disposition.blocked,
                evidence="No automated executor wired; requires human/browser-driven session with Commander in the loop.",
            )
            result.steps.append(LoopStep(cid, "blocked", "no automated executor available"))
            continue

        record = acct.record_execution(session, canonical_opportunity_id=cid, **exec_kwargs)
        result.steps.append(LoopStep(cid, "executed", record.receipt_reference))
        result.receipts.append(record.receipt_reference)

    result.finished_at = datetime.now(timezone.utc)
    return result
