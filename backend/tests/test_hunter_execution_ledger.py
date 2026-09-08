"""
Audit invariant tests for the Claude Hunter Implementation Addendum
(2026-09-08) execution ledger.

Covers every invariant listed in the addendum's "Audit invariants and
tests" section, plus a smoke check that existing Hunter lanes still
import/register cleanly alongside the new router.
"""

from __future__ import annotations

import datetime as dt
from datetime import date, timedelta

import app.main  # noqa: F401 — proves the app (incl. new router) assembles cleanly
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import (
    CanonicalOpportunity,
    Disposition,
    ExecutionRecord,
    FormalGate,
    GateVerdict,
    ReplacementChain,
    RescueAttempt,
)
from app.services import baseline_manifest, execution_accounting as acct, formal_gates, hunter_eod_report
from app.services.hunter_addendum_seed import seed_addendum_candidates
from app.services.quota_loop import run_quota_protection_loop


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_candidate(session: Session, cid: str = "TEST-CAND-001", **overrides) -> CanonicalOpportunity:
    fields = dict(
        canonical_opportunity_id=cid,
        lane="service",
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        score=50.0,
        disposition=Disposition.screened_only.value,
    )
    fields.update(overrides)
    opp = CanonicalOpportunity(**fields)
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


_NON_SUNDAY = date(2026, 9, 8)  # Tuesday
assert _NON_SUNDAY.weekday() != 6

_A_SUNDAY = date(2023, 1, 1)
assert _A_SUNDAY.weekday() == 6


# ── 1. Research cannot increment executions_today ──────────────────────


def test_research_alone_cannot_increment_execution_count():
    session = _make_session()
    _make_candidate(session)
    # Screening, scoring, rescue research — no receipt involved anywhere.
    acct.record_rescue_attempt(session, "TEST-CAND-001", "alternate_channel", "researched alternatives")
    assert acct.get_execution_count(session, _NON_SUNDAY) == 0


# ── 2. PENDING_COMMANDER cannot increment execution totals ─────────────


def test_pending_commander_does_not_increment_quota():
    session = _make_session()
    _make_candidate(session, required_commander_checkpoints="needs signature")
    acct.set_disposition(session, "TEST-CAND-001", Disposition.pending_commander)
    assert acct.get_execution_count(session, _NON_SUNDAY) == 0
    status = acct.get_quota_status(session, _NON_SUNDAY)
    assert status["execution_count"] == 0
    assert status["daily_verdict"] == "FAIL"


# ── 3. Execution without a valid receipt is rejected ────────────────────


def test_execution_requires_endpoint_and_receipt():
    session = _make_session()
    _make_candidate(session)

    with pytest.raises(acct.MissingReceiptError):
        acct.record_execution(
            session,
            canonical_opportunity_id="TEST-CAND-001",
            source="test",
            action_description="filed something",
            actions_taken="clicked submit",
            external_endpoint="",
            receipt_reference="RCPT-1",
        )

    with pytest.raises(acct.MissingReceiptError):
        acct.record_execution(
            session,
            canonical_opportunity_id="TEST-CAND-001",
            source="test",
            action_description="filed something",
            actions_taken="clicked submit",
            external_endpoint="https://example.gov/confirmation/1",
            receipt_reference="",
        )

    assert acct.get_execution_count(session, _NON_SUNDAY) == 0


def test_duplicate_receipt_rejected():
    session = _make_session()
    _make_candidate(session)
    acct.record_execution(
        session,
        canonical_opportunity_id="TEST-CAND-001",
        source="test",
        action_description="filed",
        actions_taken="submitted form",
        external_endpoint="https://example.gov/confirm/1",
        receipt_reference="RCPT-DUP",
        timestamp=dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc),
    )
    with pytest.raises(acct.MissingReceiptError):
        acct.record_execution(
            session,
            canonical_opportunity_id="TEST-CAND-001",
            source="test",
            action_description="filed again",
            actions_taken="submitted form again",
            external_endpoint="https://example.gov/confirm/1",
            receipt_reference="RCPT-DUP",
            timestamp=dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc),
        )
    assert acct.get_execution_count(session, _NON_SUNDAY) == 1


# ── 4. Realized cash cannot come from projected/hypothetical value ──────


def test_realized_cash_only_settable_via_settle_cash_with_evidence():
    session = _make_session()
    _make_candidate(session)
    record = acct.record_execution(
        session,
        canonical_opportunity_id="TEST-CAND-001",
        source="test",
        action_description="filed claim",
        actions_taken="submitted",
        external_endpoint="https://example.gov/confirm/2",
        receipt_reference="RCPT-2",
        expected_lawful_return=5000.0,
        timestamp=dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc),
    )
    # Nothing auto-populates realized cash from the projected figure.
    assert record.realized_gross_cash is None
    assert record.net_realized_cash is None

    with pytest.raises(acct.MissingReceiptError):
        acct.settle_cash(session, record.id, realized_gross_cash=5000.0, evidence_reference="")

    settled = acct.settle_cash(
        session, record.id,
        realized_gross_cash=612.40,
        settled_expenses=12.40,
        evidence_reference="bank-deposit-conf-99182",
    )
    assert settled.realized_gross_cash == 612.40
    assert settled.net_realized_cash == 600.0
    # The realized figure is independent of (and here, deliberately
    # different from) the expected_lawful_return that was on file.
    assert settled.realized_gross_cash != settled.expected_lawful_return


# ── 5. Permanent rejection requires evidence + completed rescue history ─


def test_permanent_rejection_requires_rescue_history_and_evidence():
    session = _make_session()
    _make_candidate(session)

    with pytest.raises(acct.RescueHistoryRequiredError):
        acct.set_disposition(session, "TEST-CAND-001", Disposition.rejected, evidence="unlawful, no equivalent")

    acct.record_rescue_attempt(session, "TEST-CAND-001", "official_successor", "checked for successor program", result="not_found")

    with pytest.raises(acct.RescueHistoryRequiredError):
        acct.set_disposition(session, "TEST-CAND-001", Disposition.rejected)  # no evidence text

    opp = acct.set_disposition(
        session, "TEST-CAND-001", Disposition.rejected,
        evidence="Underlying mechanism is necessarily unlawful with no lawful equivalent found.",
    )
    assert opp.disposition == Disposition.rejected.value


# ── 6. A blocked candidate opens a replacement chain ─────────────────────


def test_blocked_candidate_opens_replacement_chain():
    session = _make_session()
    _make_candidate(session)
    acct.record_rescue_attempt(session, "TEST-CAND-001", "alternate_channel", "no alternate channel found yet", result="pending")

    acct.set_disposition(session, "TEST-CAND-001", Disposition.blocked, evidence="temporarily closed")

    chains = session.exec(
        select(ReplacementChain).where(ReplacementChain.blocked_canonical_opportunity_id == "TEST-CAND-001")
    ).all()
    assert len(chains) == 1
    assert chains[0].closed_at is None

    # Idempotent — re-setting BLOCKED doesn't open a second open chain.
    acct.set_disposition(session, "TEST-CAND-001", Disposition.blocked, evidence="still closed")
    chains_after = session.exec(
        select(ReplacementChain).where(ReplacementChain.blocked_canonical_opportunity_id == "TEST-CAND-001")
    ).all()
    assert len(chains_after) == 1


def test_pending_commander_also_opens_replacement_chain():
    session = _make_session()
    _make_candidate(session)
    acct.set_disposition(session, "TEST-CAND-001", Disposition.pending_commander)
    chains = session.exec(
        select(ReplacementChain).where(ReplacementChain.blocked_canonical_opportunity_id == "TEST-CAND-001")
    ).all()
    assert len(chains) == 1


# ── 7. Every screened candidate appears exactly once in a ledger ────────


def test_every_candidate_balances_into_exactly_one_ledger():
    session = _make_session()
    _make_candidate(session, "CAND-EXEC")
    _make_candidate(session, "CAND-REJ")
    _make_candidate(session, "CAND-WATCH")
    _make_candidate(session, "CAND-SCREENED")

    acct.record_execution(
        session,
        canonical_opportunity_id="CAND-EXEC",
        source="test", action_description="x", actions_taken="y",
        external_endpoint="https://example.com/e1", receipt_reference="RCPT-EXEC",
        timestamp=dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc),
    )
    acct.record_rescue_attempt(session, "CAND-REJ", "official_successor", "checked", result="not_found")
    acct.set_disposition(session, "CAND-REJ", Disposition.rejected, evidence="no lawful equivalent exists")
    acct.set_disposition(session, "CAND-WATCH", Disposition.watchlist)
    # CAND-SCREENED left at its default SCREENED_ONLY disposition.

    recon = acct.reconciliation_ledger(session, _NON_SUNDAY)
    assert recon["screened"] == 4
    all_ids = set(recon["execution_ledger_ids"]) | set(recon["rejection_bypass_ledger_ids"])
    assert all_ids == {"CAND-EXEC", "CAND-REJ", "CAND-WATCH", "CAND-SCREENED"}
    # No overlap — each id in exactly one bucket.
    assert not (set(recon["execution_ledger_ids"]) & set(recon["rejection_bypass_ledger_ids"]))
    assert recon["execution_ledger_ids"] == ["CAND-EXEC"]
    assert recon["unresolved_follow_ups"] == []


# ── 8. Daily PASS requires five distinct execution records + receipts ───


def test_daily_pass_requires_five_distinct_receipts():
    session = _make_session()
    ts = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)
    for i in range(4):
        cid = f"CAND-{i}"
        _make_candidate(session, cid)
        acct.record_execution(
            session, canonical_opportunity_id=cid, source="test",
            action_description="x", actions_taken="y",
            external_endpoint=f"https://example.com/{i}", receipt_reference=f"RCPT-{i}",
            timestamp=ts,
        )
    status = acct.get_quota_status(session, date(2026, 9, 8))
    assert status["execution_count"] == 4
    assert status["daily_verdict"] == "FAIL"

    _make_candidate(session, "CAND-4")
    acct.record_execution(
        session, canonical_opportunity_id="CAND-4", source="test",
        action_description="x", actions_taken="y",
        external_endpoint="https://example.com/4", receipt_reference="RCPT-4",
        timestamp=ts,
    )
    status = acct.get_quota_status(session, date(2026, 9, 8))
    assert status["execution_count"] == 5
    assert status["daily_verdict"] == "PASS"


# ── 9. Existing trading / opportunity lanes remain available ────────────


def test_existing_opportunity_lanes_still_importable_alongside_new_router():
    from app.routers.opportunities import router as opportunities_router
    from app.services import daily_opportunity  # noqa: F401
    from app.routers.hunter_ledger import router as hunter_router

    assert any(r.path.endswith("/opportunities/") for r in opportunities_router.routes)
    assert any("/hunter-ops" in r.path for r in hunter_router.routes)


# ── 10. Sunday permits zero autonomous Hunter operations ────────────────


def test_sunday_blocks_execution_recording():
    session = _make_session()
    _make_candidate(session)
    sunday_ts = dt.datetime(_A_SUNDAY.year, _A_SUNDAY.month, _A_SUNDAY.day, 10, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(acct.SundayLockout):
        acct.record_execution(
            session,
            canonical_opportunity_id="TEST-CAND-001",
            source="test", action_description="x", actions_taken="y",
            external_endpoint="https://example.com/sun", receipt_reference="RCPT-SUN",
            timestamp=sunday_ts,
        )
    assert acct.get_execution_count(session, _A_SUNDAY) == 0


def test_sunday_blocks_quota_loop():
    session = _make_session()
    _make_candidate(session)
    with pytest.raises(acct.SundayLockout):
        run_quota_protection_loop(session, day=_A_SUNDAY)


# ── Quota-protection loop behavior ───────────────────────────────────────


def test_loop_executes_up_to_quota_with_stub_executor():
    session = _make_session()
    for i in range(6):
        _make_candidate(session, f"LOOP-CAND-{i}", score=float(i))

    counter = {"n": 0}

    def stub_executor(opp):
        counter["n"] += 1
        return dict(
            source="stub", action_description="stub action", actions_taken="stub taken",
            external_endpoint=f"https://example.com/loop/{opp.canonical_opportunity_id}",
            receipt_reference=f"RCPT-LOOP-{opp.canonical_opportunity_id}",
        )

    result = run_quota_protection_loop(session, day=_NON_SUNDAY, executor_fn=stub_executor)
    assert result.verdict == "PASS"
    assert result.execution_count == 5
    assert len(set(result.receipts)) == 5


def test_loop_blocks_and_opens_replacement_chain_when_no_executor_available():
    session = _make_session()
    _make_candidate(session, "LOOP-BLOCKED-1")

    result = run_quota_protection_loop(session, day=_NON_SUNDAY)  # default executor returns None
    assert result.verdict == "FAIL"
    assert result.execution_count == 0

    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "LOOP-BLOCKED-1")
    ).first()
    assert opp.disposition == Disposition.blocked.value

    attempts = session.exec(
        select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == "LOOP-BLOCKED-1")
    ).all()
    assert len(attempts) >= 1

    chains = session.exec(
        select(ReplacementChain).where(ReplacementChain.blocked_canonical_opportunity_id == "LOOP-BLOCKED-1")
    ).all()
    assert len(chains) == 1


def test_loop_routes_commander_checkpoint_to_pending_commander():
    session = _make_session()
    _make_candidate(session, "LOOP-PENDING-1", required_commander_checkpoints="needs Commander signature")

    result = run_quota_protection_loop(session, day=_NON_SUNDAY)
    assert result.verdict == "FAIL"
    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "LOOP-PENDING-1")
    ).first()
    assert opp.disposition == Disposition.pending_commander.value


# ── Baseline manifest immutability ───────────────────────────────────────


def test_baseline_manifest_freeze_is_idempotent_and_immutable():
    session = _make_session()
    entry = dict(
        baseline_index=1,
        source_profile="systemcracker_1",
        canonical_source_url="https://instagram.com/p/example1",
        source_title_or_claim="Example baseline post 1",
    )
    baseline_manifest.freeze_baseline_manifest(session, [entry])

    # Re-freezing with the same identity fields is fine and idempotent.
    baseline_manifest.freeze_baseline_manifest(session, [entry])
    rows = session.exec(select(baseline_manifest.BaselineManifestEntry)).all()
    assert len(rows) == 1

    # Attempting to change an identity field (renumbering-equivalent) fails.
    mutated = dict(entry, source_title_or_claim="A different claim entirely")
    with pytest.raises(baseline_manifest.ManifestIntegrityError):
        baseline_manifest.freeze_baseline_manifest(session, [mutated])

    # Mutable fields (screening_status) can be updated freely.
    baseline_manifest.freeze_baseline_manifest(
        session, [dict(entry, screening_status="screened_no_match")]
    )
    rows = session.exec(select(baseline_manifest.BaselineManifestEntry)).all()
    assert rows[0].screening_status == "screened_no_match"
    assert rows[0].baseline_index == 1


def test_incremental_queue_continues_index_sequence():
    session = _make_session()
    baseline_manifest.freeze_baseline_manifest(
        session,
        [
            dict(
                baseline_index=149,
                source_profile="systemcracker_1",
                canonical_source_url="https://instagram.com/p/example149",
                source_title_or_claim="Example baseline post 149",
            )
        ],
    )
    incremental = baseline_manifest.queue_incremental_post(
        session,
        source_profile="systemcracker_1",
        canonical_source_url="https://instagram.com/p/newpost",
        source_title_or_claim="A post observed after the freeze",
    )
    assert incremental.baseline_index == 150
    assert incremental.incremental_after_baseline is True


# ── Formal gates ──────────────────────────────────────────────────────────


def test_formal_gate_verdict_requires_evidence():
    session = _make_session()
    formal_gates.upsert_gate(session, "GATE-TEST-01", "Test gate")
    with pytest.raises(ValueError):
        formal_gates.record_gate_verdict(session, "GATE-TEST-01", GateVerdict.pass_, evidence_summary="")

    gate = formal_gates.record_gate_verdict(
        session, "GATE-TEST-01", GateVerdict.fail, evidence_summary="Verified route is closed with no reopening path."
    )
    assert gate.verdict == GateVerdict.fail.value
    assert gate.counts_toward_quota is False


# ── Addendum seed (today's named candidates + gates) ─────────────────────


def test_seed_addendum_candidates_creates_named_candidates_and_gates():
    session = _make_session()
    seed_addendum_candidates(session)

    ids = {
        o.canonical_opportunity_id
        for o in session.exec(select(CanonicalOpportunity)).all()
    }
    assert "HUNTER-CAND-2026-09-08-01-UCP" in ids
    assert "HUNTER-CAND-2026-09-08-02-GOOGLE" in ids
    assert "HUNTER-CAND-2026-09-08-07-TRADEMARK" in ids

    trademark = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-07-TRADEMARK"
        )
    ).first()
    # Trademark hijack tactic is rejected, but the opportunity itself is
    # NOT permanently rejected outright — it's under active test.
    assert trademark.disposition == Disposition.watchlist.value

    gates = {g.gate_id for g in session.exec(select(FormalGate)).all()}
    assert "HUNTER-OPP-2026-09-08-UCP-01" in gates
    assert "HUNTER-OPP-2026-09-08-GOOGLE-02" in gates

    # A gate PASS never counts toward the daily quota by default.
    assert acct.get_execution_count(session, _NON_SUNDAY) == 0


def test_eod_report_structure():
    session = _make_session()
    seed_addendum_candidates(session)
    report = hunter_eod_report.generate_eod_report(session, _NON_SUNDAY)
    assert report["daily_verdict"] == "FAIL"
    assert report["execution_count"] == "0 / 5"
    assert isinstance(report["rejection_bypass_ledger"], list)
    assert len(report["rejection_bypass_ledger"]) >= 7  # all seeded candidates, none executed
    assert report["execution_ledger"] == []
