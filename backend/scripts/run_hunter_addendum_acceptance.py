"""
Acceptance-test runner for the Hunter execution-ledger addendum
(2026-09-08). Seeds today's named candidates + gates, then runs Hunter's
OWN research engine (real HTTP calls, no mocks, no hand-typed findings)
against every candidate and both formal gates, and finally runs the
quota-protection loop with the research engine wired as its preflight.

Prints a plain-text report of exactly what Hunter found — including
honest failure if this environment's network egress blocks the target
sources. Nothing here is fabricated: whatever this script prints is
exactly what the research engine's real HTTP calls produced.

Usage: HUNTER_DB_PATH=<path> python scripts/run_hunter_addendum_acceptance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from app.database.config import create_db_and_tables, engine
from app.models.hunter_ledger import CanonicalOpportunity, FormalGate
from app.services.hunter_addendum_seed import seed_addendum_candidates
from app.services.hunter_eod_report import generate_eod_report
from app.services.quota_loop import run_quota_protection_loop
from app.services.research import engine as research_engine
from app.services.research.gate_runner import run_gate_acceptance_test


def main() -> None:
    create_db_and_tables()
    with Session(engine) as session:
        candidates = seed_addendum_candidates(session)
        print(f"Seeded/confirmed {len(candidates)} named candidates.\n")

        print("=== Hunter's own research engine — per-candidate run ===")
        for opp in session.exec(select(CanonicalOpportunity)).all():
            result = research_engine.run_research(session, opp.canonical_opportunity_id)
            print(f"\n[{opp.canonical_opportunity_id}]")
            print(f"  passed={result.passed} network_ok={result.network_ok} "
                  f"disposition_override={result.disposition_override}")
            print(f"  commander_checkpoint={result.commander_checkpoint}")
            print(f"  notes={result.notes[:300]}")

        print("\n=== Formal gate acceptance tests (Hunter-produced verdicts) ===")
        for gate in session.exec(select(FormalGate)).all():
            updated = run_gate_acceptance_test(session, gate.gate_id)
            print(f"\n[{updated.gate_id}] {updated.title}")
            print(f"  verdict={updated.verdict} counts_toward_quota={updated.counts_toward_quota}")
            print(f"  evidence_summary={updated.evidence_summary}")

        print("\n=== Quota-protection loop (Hunter's own preflight, no fabricated executor) ===")
        loop_result = run_quota_protection_loop(
            session,
            preflight_fn=research_engine.make_research_preflight(session),
        )
        print(f"verdict={loop_result.verdict} execution_count={loop_result.execution_count}")
        for step in loop_result.steps:
            print(f"  {step.canonical_opportunity_id or '(none)'}: {step.outcome} — {step.detail[:200]}")

        print("\n=== End-of-day report ===")
        report = generate_eod_report(session)
        print(f"Daily verdict: {report['daily_verdict']}")
        print(f"Execution count: {report['execution_count']}")
        print(f"Weekly count: {report['weekly_count']}")
        print(f"Net realized cash today: {report['net_realized_cash_today']}")
        print(f"Pending probability-adjusted value: {report['pending_probability_adjusted_value']}")
        print(f"Reconciliation: {report['reconciliation']}")


if __name__ == "__main__":
    main()
