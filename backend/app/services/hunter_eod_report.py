"""
Mandatory end-of-day output (Claude Hunter Implementation Addendum).

Builds the report structure required by the addendum: opening verdict
block, execution ledger, rejection/bypass ledger, balanced reconciliation,
and forward-looking sections. Every screened CanonicalOpportunity lands in
exactly one of the two ledgers (see execution_accounting.reconciliation_ledger).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.hunter_ledger import (
    CanonicalOpportunity,
    Disposition,
    ExecutionRecord,
    FormalGate,
    ReplacementChain,
    RescueAttempt,
)
from app.services import execution_accounting as acct


def generate_eod_report(session: Session, day: Optional[date] = None) -> dict:
    d = day or datetime.now(timezone.utc).date()
    quota = acct.get_quota_status(session, d)
    reconciliation = acct.reconciliation_ledger(session, d)

    start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    executions_today = session.exec(
        select(ExecutionRecord).where(
            ExecutionRecord.timestamp >= start, ExecutionRecord.timestamp < end
        )
    ).all()

    execution_ledger = [
        {
            "canonical_opportunity_id": e.canonical_opportunity_id,
            "source": e.source,
            "described_action": e.action_description,
            "actions_actually_taken": e.actions_taken,
            "external_endpoint": e.external_endpoint,
            "timestamp": e.timestamp.isoformat(),
            "receipt": e.receipt_reference,
            "money_spent_or_committed": e.money_spent_committed,
            "expected_lawful_return": e.expected_lawful_return,
            "time_to_cash": e.expected_time_to_cash,
            "follow_up": e.follow_up,
            "owner": e.owner,
            "due_date": e.due_date.isoformat() if e.due_date else None,
            "realized_gross_cash": e.realized_gross_cash,
            "net_realized_cash": e.net_realized_cash,
        }
        for e in executions_today
    ]

    opportunities = session.exec(select(CanonicalOpportunity)).all()
    rejection_bypass_ledger = []
    for opp in opportunities:
        if opp.disposition == Disposition.executed.value:
            continue
        attempts = session.exec(
            select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == opp.canonical_opportunity_id)
        ).all()
        chain = session.exec(
            select(ReplacementChain).where(
                ReplacementChain.blocked_canonical_opportunity_id == opp.canonical_opportunity_id
            )
        ).first()
        if opp.disposition == Disposition.rejected.value:
            status = "permanent"
        elif opp.disposition in (Disposition.blocked.value, Disposition.pending_commander.value):
            status = "curable"
        else:
            status = "temporary"
        rejection_bypass_ledger.append(
            {
                "source_index_or_link": opp.source_post_refs,
                "canonical_opportunity_id": opp.canonical_opportunity_id,
                "disposition": opp.disposition,
                "facts_and_evidence": opp.factual_mechanism,
                "rescue_paths_and_results": [
                    {"type": a.rescue_type, "result": a.result, "evidence": a.evidence_reference}
                    for a in attempts
                ],
                "permanent_curable_or_temporary": status,
                "next_action": opp.next_action,
                "owner": opp.owner,
                "due_date": opp.due_date.isoformat() if opp.due_date else None,
                "replacement_candidate": chain.replacement_canonical_opportunity_id if chain else None,
                "final_replacement_chain_result": chain.result if chain else None,
            }
        )

    gates = session.exec(select(FormalGate)).all()
    gate_summaries = [
        {
            "gate_id": g.gate_id,
            "title": g.title,
            "verdict": g.verdict,
            "counts_toward_quota": g.counts_toward_quota,
            "evidence_summary": g.evidence_summary,
        }
        for g in gates
    ]

    commander_decisions_needed = [
        {
            "canonical_opportunity_id": opp.canonical_opportunity_id,
            "checkpoint": opp.required_commander_checkpoints,
            "next_action": opp.next_action,
        }
        for opp in opportunities
        if opp.disposition == Disposition.pending_commander.value and opp.required_commander_checkpoints
    ]

    return {
        "daily_verdict": quota["daily_verdict"],
        "execution_count": f"{quota['execution_count']} / {quota['execution_quota']}",
        "weekly_count": f"{quota['weekly_count']} / {quota['weekly_quota']}",
        "cycle_count": f"{quota['cycle_count']} / {quota['cycle_quota']}",
        "net_realized_cash_today": quota["net_realized_cash_today"],
        "pending_probability_adjusted_value": quota["pending_probability_adjusted_value"],
        "execution_ledger": execution_ledger,
        "rejection_bypass_ledger": rejection_bypass_ledger,
        "reconciliation": reconciliation,
        "formal_gates": gate_summaries,
        "commander_decisions_needed": commander_decisions_needed,
    }
