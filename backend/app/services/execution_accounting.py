"""
Execution accounting for the Hunter Implementation Addendum (2026-09-08).

This is the enforcement layer: the only place execution credit, realized
cash, permanent rejection, and replacement-chain accounting are allowed to
change. Routers and the quota loop must call through here rather than
writing to the ledger tables directly, or the invariants below stop
holding.

Invariants enforced here (see tests/test_hunter_execution_ledger.py):
  1. A research/scoring/screening action alone can never create an
     ExecutionRecord — only record_execution() can, and it requires a
     real external_endpoint + receipt_reference.
  2. PENDING_COMMANDER never increments the quota — there is no code path
     from "pending" straight into a counted execution without going
     through record_execution() with a receipt.
  3. record_execution() without a valid receipt raises ValueError.
  4. Realized cash is only ever written by settle_cash(), which requires
     its own evidence_reference distinct from any projected figure.
  5. Permanent REJECTED/BLOCKED requires an evidenced rescue history.
  6. Any BLOCKED/PENDING_COMMANDER disposition opens a replacement chain.
  7. Every CanonicalOpportunity carries exactly one current disposition
     (non-nullable column), so it lands in exactly one ledger bucket.
  8. Daily PASS requires 5 distinct ExecutionRecord rows with 5 distinct
     receipts on that day.
  10. Autonomous execution/rescue actions are refused on Sundays.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.hunter_ledger import (
    CanonicalOpportunity,
    Disposition,
    DISPOSITIONS_REQUIRING_REPLACEMENT,
    DISPOSITIONS_REQUIRING_RESCUE_HISTORY,
    ExecutionRecord,
    RescueAttempt,
    RescueResult,
    ReplacementChain,
    ZERO_EXECUTION_DISPOSITIONS,
)

DAILY_EXECUTION_QUOTA = 5
WEEKLY_EXECUTION_QUOTA = 25
CYCLE_EXECUTION_QUOTA = 100
CYCLE_DAYS = 28

# Commander's 5/day, 25/week, 100/4-week target is scoped to the
# compliance-recovery ("149 campaign") ledger only. Equities trading has
# its own, entirely separate accounting (ProviderExecution/ExecutionOutcome
# in app.services.execution) and must never count toward this quota.
#
# Correction (Commander, 2026-09-10): a blocklist (QUOTA_EXCLUDED_LANES =
# {"trading"}) is insufficient — it silently counts anything that ISN'T
# named "trading" (a future crypto lane, a mistagged row, an unexpected
# lane string), on the unproven assumption that everything else belongs
# to the campaign. This is a positive allowlist instead: only these lanes
# are the actual 149/compliance-recovery campaign, per every candidate
# app.services.hunter_addendum_seed currently seeds
# ("compliance_recovery", "legal_claims", "data_service", "service" — the
# UCP-01/GOOGLE-02/PUBLICRECORDS/QRRE/GHOSTJOB/TRADEMARK candidates;
# DARKPOOL is lane="trading" and deliberately excluded). A lane not in
# this set — including "trading", "crypto", or any future/unexpected
# value — does not count, full stop; nothing is exempted by name alone.
CAMPAIGN_LANES = {"compliance_recovery", "legal_claims", "data_service", "service"}


class SundayLockout(PermissionError):
    """Raised when an autonomous action is attempted on a Sunday."""


class MissingReceiptError(ValueError):
    """Raised when an execution is attempted without a valid receipt."""


class RescueHistoryRequiredError(ValueError):
    """Raised when a permanent rejection/block is attempted without an
    evidenced rescue history."""


def assert_autonomous_operations_allowed(on: Optional[date] = None) -> None:
    """Sunday permits zero autonomous Hunter operations (addendum test
    invariant). Reporting/reads are unaffected — only call this from
    write paths that represent autonomous action."""
    d = on or datetime.now(timezone.utc).date()
    if d.weekday() == 6:  # Monday=0 ... Sunday=6
        raise SundayLockout(
            "Sunday permits zero autonomous Hunter operations. "
            "This action requires a non-Sunday operating day."
        )


# ── Execution ledger (the only source of quota credit) ────────────────────


def record_execution(
    session: Session,
    *,
    canonical_opportunity_id: str,
    source: str,
    action_description: str,
    actions_taken: str,
    external_endpoint: str,
    receipt_reference: str,
    money_spent_committed: float = 0.0,
    expected_lawful_return: float = 0.0,
    expected_time_to_cash: Optional[str] = None,
    follow_up: Optional[str] = None,
    owner: str = "Hunter",
    due_date: Optional[date] = None,
    timestamp: Optional[datetime] = None,
    enforce_sunday_lockout: bool = True,
) -> ExecutionRecord:
    """Record one countable execution. Requires the action to have
    actually reached an external, verifiable endpoint with a receipt —
    research, drafts, and approval-ready packets must not call this."""
    if enforce_sunday_lockout:
        assert_autonomous_operations_allowed((timestamp or datetime.now(timezone.utc)).date() if timestamp else None)

    if not external_endpoint or not external_endpoint.strip():
        raise MissingReceiptError(
            "record_execution() requires a real external_endpoint — "
            "research or drafts do not count."
        )
    if not receipt_reference or not receipt_reference.strip():
        raise MissingReceiptError(
            "record_execution() requires a non-empty receipt_reference "
            "(confirmation number, timestamped submission, sent-message "
            "record, reachable live URL, filing/order receipt, or "
            "equivalent proof)."
        )

    existing = session.exec(
        select(ExecutionRecord).where(ExecutionRecord.receipt_reference == receipt_reference)
    ).first()
    if existing:
        raise MissingReceiptError(
            f"receipt_reference '{receipt_reference}' is already recorded "
            f"(ExecutionRecord id={existing.id}) — duplicate receipts do "
            "not create additional execution credit."
        )

    opp = _get_opportunity(session, canonical_opportunity_id)

    record = ExecutionRecord(
        canonical_opportunity_id=canonical_opportunity_id,
        source=source,
        action_description=action_description,
        actions_taken=actions_taken,
        external_endpoint=external_endpoint.strip(),
        receipt_reference=receipt_reference.strip(),
        money_spent_committed=money_spent_committed,
        expected_lawful_return=expected_lawful_return,
        expected_time_to_cash=expected_time_to_cash,
        follow_up=follow_up,
        owner=owner,
        due_date=due_date,
        timestamp=timestamp or datetime.now(timezone.utc),
    )
    session.add(record)

    if opp:
        opp.disposition = Disposition.executed
        opp.execution_endpoint = record.external_endpoint
        opp.external_receipt_ref = record.receipt_reference
        opp.updated_at = datetime.now(timezone.utc)
        session.add(opp)

    session.commit()
    session.refresh(record)
    return record


def settle_cash(
    session: Session,
    execution_record_id: int,
    *,
    realized_gross_cash: float,
    evidence_reference: str,
    settled_expenses: float = 0.0,
) -> ExecutionRecord:
    """Attach the actual, evidenced cash outcome to a prior execution.
    This is the ONLY function permitted to populate realized_gross_cash —
    it takes its own evidence_reference and never reads expected_lawful_return
    or any other projected figure as a source of truth."""
    if not evidence_reference or not evidence_reference.strip():
        raise MissingReceiptError(
            "settle_cash() requires an evidence_reference distinct from "
            "any projected/advertised/hypothetical figure — e.g. a bank "
            "deposit confirmation, statement line, or payout receipt."
        )

    record = session.get(ExecutionRecord, execution_record_id)
    if not record:
        raise ValueError(f"ExecutionRecord id={execution_record_id} not found")

    record.realized_gross_cash = realized_gross_cash
    record.settled_expenses = settled_expenses
    record.net_realized_cash = realized_gross_cash - settled_expenses
    record.settled_evidence_reference = evidence_reference.strip()
    record.settled_at = datetime.now(timezone.utc)
    session.add(record)

    opp = _get_opportunity(session, record.canonical_opportunity_id)
    if opp:
        opp.realized_gross_cash = record.realized_gross_cash
        opp.settled_expenses = record.settled_expenses
        opp.net_realized_cash = record.net_realized_cash
        opp.updated_at = datetime.now(timezone.utc)
        session.add(opp)

    session.commit()
    session.refresh(record)
    return record


# ── Disposition / screening ────────────────────────────────────────────────


def set_disposition(
    session: Session,
    canonical_opportunity_id: str,
    disposition: Disposition | str,
    *,
    evidence: Optional[str] = None,
    duplicate_of: Optional[str] = None,
    new_checkpoint: Optional[str] = None,
) -> CanonicalOpportunity:
    """Move a candidate to a new disposition, enforcing the rescue-history
    and replacement-chain rules. Never call this to set EXECUTED directly —
    that is set only by record_execution().

    `new_checkpoint`, when moving to PENDING_COMMANDER, replaces
    required_commander_checkpoints with the specific ask for this round —
    e.g. re-opening a candidate that was already answered once, now
    needing a different piece of information."""
    disposition = Disposition(disposition)
    if disposition == Disposition.executed:
        raise ValueError(
            "EXECUTED may only be set via record_execution() with a real "
            "receipt — set_disposition() cannot grant execution credit."
        )

    opp = _get_opportunity(session, canonical_opportunity_id, required=True)

    if disposition in DISPOSITIONS_REQUIRING_RESCUE_HISTORY:
        attempts = list(
            session.exec(
                select(RescueAttempt).where(
                    RescueAttempt.canonical_opportunity_id == canonical_opportunity_id
                )
            )
        )
        if not attempts:
            raise RescueHistoryRequiredError(
                f"Cannot set '{disposition.value}' on {canonical_opportunity_id} "
                "without at least one documented RescueAttempt — feasibility "
                "analysis alone is diagnostic, not completion."
            )
        if disposition == Disposition.rejected and not evidence:
            raise RescueHistoryRequiredError(
                "Permanent REJECTED requires an 'evidence' statement showing "
                "the mechanism is unlawful with no lawful equivalent, every "
                "successor is closed with no reopening path, Commander is "
                "factually ineligible with no adjacent route, or bounded "
                "economics fail after lower-cost alternatives are exhausted."
            )

    if disposition == Disposition.duplicate and not duplicate_of:
        raise ValueError("DUPLICATE requires duplicate_of (the canonical opportunity it merges into).")

    opp.disposition = disposition.value
    if evidence:
        note = f"[disposition={disposition.value}] {evidence}"
        opp.next_action = note if not opp.next_action else f"{opp.next_action}\n{note}"
    if duplicate_of:
        opp.duplicate_of_canonical_opportunity_id = duplicate_of
    if disposition == Disposition.pending_commander and new_checkpoint:
        opp.required_commander_checkpoints = new_checkpoint
        # This is a fresh ask — clear any prior answer so the decisions
        # feed (which filters on commander_response is None) surfaces it.
        opp.commander_response = None
        opp.commander_responded_at = None
    opp.updated_at = datetime.now(timezone.utc)
    session.add(opp)
    session.commit()
    session.refresh(opp)

    if disposition in DISPOSITIONS_REQUIRING_REPLACEMENT:
        open_replacement_chain(session, canonical_opportunity_id)

    if disposition == Disposition.pending_commander and opp.commander_response is None:
        # Commander has no way to know a checkpoint is waiting unless
        # something outside the chat widget tells them — notify now,
        # same channel already used for high/critical alerts.
        try:
            from app.services import alerts as alert_svc
            alert_svc.raise_alert(
                alert_type="review_required",
                title=f"Hunter needs your input — {canonical_opportunity_id}",
                body=(opp.required_commander_checkpoints or "Open the Hunter AI chat for details.")[:300],
                session=session,
                priority="high",
                source_id=canonical_opportunity_id,
            )
        except Exception:  # noqa: BLE001
            # Never let a notification failure block the disposition change.
            pass

    return opp


def open_replacement_chain(
    session: Session,
    blocked_canonical_opportunity_id: str,
    replacement_canonical_opportunity_id: Optional[str] = None,
) -> ReplacementChain:
    """Ensure a blocked/pending-Commander candidate has an open parallel
    replacement chain so the operating day isn't consumed by one blocker.
    Idempotent — returns the existing open chain if one is already there."""
    existing = session.exec(
        select(ReplacementChain).where(
            ReplacementChain.blocked_canonical_opportunity_id == blocked_canonical_opportunity_id,
            ReplacementChain.closed_at.is_(None),
        )
    ).first()
    if existing:
        if replacement_canonical_opportunity_id and not existing.replacement_canonical_opportunity_id:
            existing.replacement_canonical_opportunity_id = replacement_canonical_opportunity_id
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing

    chain = ReplacementChain(
        blocked_canonical_opportunity_id=blocked_canonical_opportunity_id,
        replacement_canonical_opportunity_id=replacement_canonical_opportunity_id,
    )
    session.add(chain)
    session.commit()
    session.refresh(chain)
    return chain


def close_replacement_chain(session: Session, chain_id: int, *, result: str) -> ReplacementChain:
    chain = session.get(ReplacementChain, chain_id)
    if not chain:
        raise ValueError(f"ReplacementChain id={chain_id} not found")
    chain.closed_at = datetime.now(timezone.utc)
    chain.result = result
    session.add(chain)
    session.commit()
    session.refresh(chain)
    return chain


def record_commander_answer(
    session: Session,
    canonical_opportunity_id: str,
    answer: str,
    *,
    decision: Optional[str] = None,
) -> CanonicalOpportunity:
    """Record Commander's reply to a checkpoint an opportunity is waiting
    on (the chat/decisions UI). This is the only path that writes
    commander_response — never inferred from freeform chat text by an LLM.

    `decision` drives what happens next:
      - "decline" -> REJECTED. Safe to do directly: anything that reached
        PENDING_COMMANDER already has a logged rescue attempt from the
        research engine, so the rescue-history invariant holds.
      - "approve" or omitted (just supplying requested info) -> WATCHLIST:
        the checkpoint itself is answered, but reaching EXECUTED still
        needs a real execution capability for this opportunity, which may
        not exist yet — Commander's answer alone never grants execution
        credit.
    """
    if not answer or not answer.strip():
        raise ValueError("record_commander_answer() requires non-empty answer text.")

    opp = _get_opportunity(session, canonical_opportunity_id, required=True)
    opp.commander_response = answer.strip()
    opp.commander_responded_at = datetime.now(timezone.utc)
    session.add(opp)
    session.commit()

    if decision == "decline":
        return set_disposition(
            session, canonical_opportunity_id, Disposition.rejected,
            evidence=f"Commander declined: {answer.strip()}",
        )
    return set_disposition(
        session, canonical_opportunity_id, Disposition.watchlist,
        evidence=f"Commander responded: {answer.strip()}",
    )


# Commander, 2026-09-11: the UCP-01 government-portal-search dispatch was
# using commander_response verbatim as the literal business_name to
# search for — with no check that it actually WAS a business name.
# Real production evidence: a search was dispatched with
# business_name="skip" (Commander's explicit decline, typed as free
# text after two earlier answers didn't visibly lead anywhere), and
# again with business_name="Approved — proceed." (an approval-only
# reply with no business name in it at all).
_UCP_DECLINE_ANSWERS = {"skip", "n/a", "na", "none"}
_UCP_APPROVAL_ONLY_ANSWERS = {"approve", "approved", "approved — proceed.", "approved - proceed."}
UCP_BUSINESS_NAME_ASK_MARKER = "business name or FEIN"


def resolve_ucp_business_name_checkpoint(
    session: Session, opp: CanonicalOpportunity
) -> Optional[str]:
    """Returns the business name to search Georgia's unclaimed-property
    portal for, only when Commander's latest answer is actually a
    business name. Otherwise handles the checkpoint directly and returns
    None so the caller never dispatches a search with the wrong input:

    - "skip" (or a clear equivalent) is Commander's own decision to drop
      this checkpoint — honored silently: never used as search input,
      and never re-asked again (nothing here changes disposition or
      commander_response, so this same branch is taken on every future
      boot too, with no repeated prompting).
    - An approval-only reply ("Approved — proceed.") means Commander
      wants to continue but has not yet supplied the actually-needed
      business name — reopens the checkpoint with that specific,
      narrower ask (the same reopen-with-specific-ask pattern already
      used for the GOOGLE-02 checkpoint) rather than misusing the
      approval phrase as search input. set_disposition() with
      new_checkpoint clears commander_response, so a genuinely new
      answer resurfaces in the decisions feed and is honored on the
      next boot without needing Commander to resubmit anything already
      given (identity fields are untouched by this).
    - Anything else is treated as a real, substantive answer and
      returned for use as the search's business_name — unchanged from
      prior behavior.
    """
    response_text = (opp.commander_response or "").strip()
    if not response_text:
        return None
    normalized = response_text.lower()
    if normalized in _UCP_DECLINE_ANSWERS:
        return None
    if normalized in _UCP_APPROVAL_ONLY_ANSWERS:
        already_reopened = UCP_BUSINESS_NAME_ASK_MARKER in (opp.required_commander_checkpoints or "")
        if not already_reopened:
            set_disposition(
                session, opp.canonical_opportunity_id, Disposition.pending_commander,
                evidence=(
                    "Commander approved proceeding but did not supply the business "
                    "name/FEIN this search needs."
                ),
                new_checkpoint=(
                    "To search Georgia's unclaimed property portal, Hunter needs the "
                    "specific business name or FEIN to search for — approval alone "
                    "isn't enough input. Reply with that name/FEIN, or reply 'skip' "
                    "to drop this checkpoint."
                ),
            )
        return None
    return response_text


def record_rescue_attempt(
    session: Session,
    canonical_opportunity_id: str,
    rescue_type: str,
    description: str,
    *,
    result: str = RescueResult.pending,
    evidence_reference: Optional[str] = None,
) -> RescueAttempt:
    attempt = RescueAttempt(
        canonical_opportunity_id=canonical_opportunity_id,
        rescue_type=rescue_type,
        description=description,
        result=result,
        evidence_reference=evidence_reference,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


# ── Quota / reconciliation ────────────────────────────────────────────────


def _campaign_scoped_receipts(session: Session, start: datetime, end: datetime) -> set[str]:
    """DISTINCT execution receipts in [start, end) that provably belong to
    the 149/compliance-recovery campaign — positive membership: a receipt
    only counts when its linked CanonicalOpportunity resolves AND its
    lane is in CAMPAIGN_LANES. Anything unresolvable or out of scope
    fails closed (does not count) rather than being counted by default —
    "other activity does not count merely because it shares a table"
    (Commander, 2026-09-10). A resolvable-but-orphaned receipt (the
    opportunity row is missing — should not happen given
    record_execution() always requires a real canonical_opportunity_id,
    but data can drift) is a genuine reconciliation problem, not evidence
    of campaign work, so it does not count either."""
    rows = session.exec(
        select(ExecutionRecord).where(
            ExecutionRecord.timestamp >= start,
            ExecutionRecord.timestamp < end,
        )
    ).all()
    receipts: set[str] = set()
    for r in rows:
        if not r.receipt_reference:
            continue
        opp = _get_opportunity(session, r.canonical_opportunity_id)
        if opp is None or opp.lane not in CAMPAIGN_LANES:
            continue
        receipts.add(r.receipt_reference)
    return receipts


def get_execution_count(session: Session, day: Optional[date] = None) -> int:
    """Count DISTINCT, campaign-scoped execution receipts for the given
    day (default: today). This is the only function the quota check
    should read from."""
    d = day or datetime.now(timezone.utc).date()
    start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return len(_campaign_scoped_receipts(session, start, end))


def get_weekly_execution_count(session: Session, day: Optional[date] = None) -> int:
    d = day or datetime.now(timezone.utc).date()
    week_start = d - timedelta(days=d.weekday())
    start = datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return len(_campaign_scoped_receipts(session, start, end))


def get_cycle_execution_count(session: Session, day: Optional[date] = None, cycle_start: Optional[date] = None) -> int:
    d = day or datetime.now(timezone.utc).date()
    cs = cycle_start or (d - timedelta(days=d.weekday()) - timedelta(weeks=3))
    start = datetime.combine(cs, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=CYCLE_DAYS)
    return len(_campaign_scoped_receipts(session, start, end))


def get_quota_status(session: Session, day: Optional[date] = None) -> dict:
    d = day or datetime.now(timezone.utc).date()
    daily = get_execution_count(session, d)
    weekly = get_weekly_execution_count(session, d)
    cycle = get_cycle_execution_count(session, d)
    distinct_receipts = daily  # get_execution_count already dedupes by receipt
    verdict = "PASS" if (daily >= DAILY_EXECUTION_QUOTA and distinct_receipts >= DAILY_EXECUTION_QUOTA) else "FAIL"
    return {
        "date": d.isoformat(),
        "daily_verdict": verdict,
        "execution_count": daily,
        "execution_quota": DAILY_EXECUTION_QUOTA,
        "weekly_count": weekly,
        "weekly_quota": WEEKLY_EXECUTION_QUOTA,
        "cycle_count": cycle,
        "cycle_quota": CYCLE_EXECUTION_QUOTA,
        "net_realized_cash_today": get_net_realized_cash(session, d),
        "pending_probability_adjusted_value": get_pending_probability_adjusted_value(session),
    }


def get_net_realized_cash(session: Session, day: Optional[date] = None) -> float:
    """Sum of net_realized_cash for executions SETTLED on the given day —
    never derived from expected/projected figures."""
    d = day or datetime.now(timezone.utc).date()
    start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    rows = session.exec(
        select(ExecutionRecord).where(
            ExecutionRecord.settled_at.is_not(None),
            ExecutionRecord.settled_at >= start,
            ExecutionRecord.settled_at < end,
        )
    ).all()
    return round(sum(r.net_realized_cash or 0.0 for r in rows), 2)


def get_pending_probability_adjusted_value(session: Session) -> float:
    rows = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.disposition.notin_(
                [Disposition.executed.value, Disposition.rejected.value, Disposition.duplicate.value]
            )
        )
    ).all()
    return round(sum(r.probability_adjusted_pending_value or 0.0 for r in rows), 2)


def reconciliation_ledger(session: Session, day: Optional[date] = None) -> dict:
    """Balance every screened CanonicalOpportunity into exactly one of the
    execution ledger or the rejection/bypass ledger — invariant #7.

    The addendum's own vocabulary calls the non-execution bucket the
    "rejection/bypass ledger" (every viewed-but-unexecuted candidate), but
    that is a bucket name, not a verdict — most entries in it were never
    substantively evaluated as bad. `by_disposition` and
    `research_incomplete` make that distinction explicit so a caller can't
    misread "not executed" as "Hunter rejected it."
    """
    opportunities = session.exec(select(CanonicalOpportunity)).all()

    execution_ledger = []
    rejection_bypass_ledger = []
    unresolved = []
    by_disposition: dict[str, int] = {}

    for opp in opportunities:
        if opp.disposition is None:
            unresolved.append(opp.canonical_opportunity_id)
            continue
        by_disposition[opp.disposition] = by_disposition.get(opp.disposition, 0) + 1
        if opp.disposition == Disposition.executed.value:
            execution_ledger.append(opp.canonical_opportunity_id)
        else:
            rejection_bypass_ledger.append(opp.canonical_opportunity_id)

    research_incomplete = sum(
        by_disposition.get(d.value, 0)
        for d in (Disposition.pending_research, Disposition.blocked_infrastructure, Disposition.blocked_capability)
    )

    chains = session.exec(select(ReplacementChain)).all()
    return {
        "screened": len(opportunities),
        "executed": len(execution_ledger),
        "not_yet_executed": len(rejection_bypass_ledger),
        "by_disposition": by_disposition,
        # Research not yet complete (awaiting research, or Hunter's
        # research couldn't reach the network / has no capability wired
        # yet) — these are NOT substantive findings about the opportunity
        # and must not be read as rejections.
        "research_incomplete": research_incomplete,
        # Only a REJECTED disposition is a substantive, evidenced,
        # rescue-history-backed call that the opportunity itself is dead.
        "substantively_rejected": by_disposition.get(Disposition.rejected.value, 0),
        "duplicate": by_disposition.get(Disposition.duplicate.value, 0),
        "blocked_or_pending_commander": sum(
            by_disposition.get(d.value, 0)
            for d in (Disposition.blocked, Disposition.pending_commander)
        ),
        "deferred": by_disposition.get(Disposition.deferred_for_higher_value.value, 0),
        "replacement_chains_opened": len(chains),
        "replacement_chains_closed": sum(1 for c in chains if c.closed_at is not None),
        "unresolved_follow_ups": unresolved,
        "execution_ledger_ids": execution_ledger,
        "rejection_bypass_ledger_ids": rejection_bypass_ledger,
    }


# ── helpers ─────────────────────────────────────────────────────────────


def _get_opportunity(
    session: Session, canonical_opportunity_id: str, *, required: bool = False
) -> Optional[CanonicalOpportunity]:
    opp = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == canonical_opportunity_id
        )
    ).first()
    if required and not opp:
        raise ValueError(f"CanonicalOpportunity '{canonical_opportunity_id}' not found")
    return opp
