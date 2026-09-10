import asyncio
import os
import logging

# Root logger has no handler by default under uvicorn — without this, every
# logger.info() call in the scheduler tasks (daily_scan_task, recycle_cycle_task,
# etc.) silently vanishes. This was masking a live trading stall for weeks.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.database.config import create_db_and_tables
from app.routers.autotrader import router as autotrader_router
from app.routers.budget import router as budget_router
from app.routers.opportunities import router as opportunities_router
from app.routers.reports import router as reports_router
from app.routers.alerts import router as alerts_router
from app.routers.packets import router as packets_router
from app.routers.strategies import router as strategies_router
from app.routers.operations import router as operations_router
from app.routers.execution import router as execution_router
from app.routers.advisors import router as advisors_router
from app.routers.sources import router as sources_router
from app.routers.performance import router as performance_router
from app.routers.system import router as system_router
from app.routers.monitoring import router as monitoring_router
from app.routers.handoff import router as handoff_router
from app.routers.leads import router as leads_router
from app.routers.decisions import router as decisions_router
from app.routers.marketplace import router as marketplace_router
from app.routers.tasks import router as tasks_router
from app.routers.auth import router as auth_router
from app.routers.diag import router as diag_router
from app.routers.signals import router as signals_router
from app.routers.forge import router as forge_router
from app.routers.quickcash import router as quickcash_router
from app.routers.store import router as store_router
from app.routers.assistant import router as assistant_router
from app.routers.policy import router as policy_router
from app.routers.hunter_ledger import router as hunter_ledger_router
from app.routers.commander_documents import router as commander_documents_router
from app.models.policy_event import PolicyEvent  # noqa: F401 — registers table
from app.models.created_product import CreatedProduct  # noqa
from app.models.campaign_brief import CampaignBrief  # noqa: F401 — registers table
from app.models.commander_document import CommanderDocument  # noqa: F401 — registers table
from app.services.scheduler import scheduler, daily_scan_task, weekly_report_task, recycle_cycle_task, leon_daily_commerce_task, policy_scan_task, discovery_scan_task, signal_scan_task, morning_report_task, ledger_recovery_loop_task, checkpoint_resume_task, task_retry_sweep_task
from app.config import RECYCLE_CYCLE_INTERVAL_SECONDS, STRATEGY_MODE, ALPACA_ENABLED, DISCOVERY_SCAN_INTERVAL_SECONDS, SIGNAL_SCAN_INTERVAL_SECONDS, MORNING_REPORT_HOUR, MORNING_REPORT_MINUTE, LEDGER_LOOP_INTERVAL_SECONDS, CHECKPOINT_RESUME_INTERVAL_SECONDS, TASK_RETRY_SWEEP_INTERVAL_SECONDS, RUN_BROKER_HISTORY_RECONCILIATION_ON_STARTUP

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = _BACKEND_DIR / "frontend_dist"
_SCHEDULER_TZ = "America/New_York"
_startup_logger = logging.getLogger(__name__)


def _bootstrap_intake_after_startup() -> None:
    """Run optional opportunity bootstrap after the web app is already healthy."""
    try:
        from app.database.config import engine
        from app.services.autotrader import bootstrap_intake
        with Session(engine) as session:
            bootstrap_intake(session)
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "bootstrap_intake failed after startup — %s: %s",
            type(exc).__name__,
            exc,
        )


def _bootstrap_hunter_ledger_actions_after_startup() -> None:
    """Carry Commander-approved checkpoints through to the furthest
    lawful, externally effective endpoint from Hunter's own runtime —
    not a manual API call from Claude or Commander. Idempotent per day;
    safe to run on every deploy/restart.

    1. UCP-01: dispatches a portal search using Commander's supplied
       business name. Once Commander's identity fields are on file
       (app/services/commander_identity.py), the same task continues
       past the search into filing the claim if a matching record is
       found — no separate approval round; see
       app/worker/executors.py::_execute_government_portal_search.
    2. GOOGLE-02: reopens the checkpoint with the specific personal-data
       ask if Commander's prior answer didn't supply it. Once Commander
       has answered AND the structured identity fields (name + email or
       phone) are on file, dispatches the intake-form submission
       directly — Hunter never invents identity data, but once
       Commander has supplied it, submission is autonomous."""
    try:
        from datetime import date as _date

        from sqlmodel import select as _select

        from app.database.config import engine
        from app.models.hunter_ledger import CanonicalOpportunity, Disposition
        from app.services import commander_identity
        from app.services import execution_accounting as acct
        from app.services import tasks as task_svc
        from app.services.research.providers.ga_unclaimed_property import PORTAL_URL
        from app.services.research.providers.google_incognito import INTAKE_URL

        def _available_identity_fields(fields: list[str]) -> dict[str, str]:
            out: dict[str, str] = {}
            for f in fields:
                v = commander_identity.get_identity_field(f)
                if v:
                    out[f] = v
            return out

        with Session(engine) as session:
            try:
                acct.assert_autonomous_operations_allowed()
            except acct.SundayLockout:
                return

            ucp = session.exec(
                _select(CanonicalOpportunity).where(
                    CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-01-UCP"
                )
            ).first()
            if ucp and ucp.commander_response and ucp.disposition != Disposition.executed.value:
                identity_fields = _available_identity_fields(
                    ["full_name", "address_line1", "address_line2", "city", "state", "zip"]
                )
                task_svc.dispatch_task(
                    task_type="government_portal_search",
                    spec_payload={
                        "search_url": PORTAL_URL,
                        "business_name": ucp.commander_response,
                        "canonical_opportunity_id": ucp.canonical_opportunity_id,
                        "identity_fields": identity_fields,
                    },
                    session=session,
                    source_type="canonical_opportunity",
                    source_id=ucp.canonical_opportunity_id,
                    priority=10,
                    idempotency_key=f"gov-search:{ucp.canonical_opportunity_id}:{_date.today().isoformat()}",
                    max_attempts=2,
                )

            google = session.exec(
                _select(CanonicalOpportunity).where(
                    CanonicalOpportunity.canonical_opportunity_id == "HUNTER-CAND-2026-09-08-02-GOOGLE"
                )
            ).first()
            _google_specific_ask_marker = "full legal name"
            if google and google.disposition != Disposition.executed.value:
                already_reopened_with_specific_ask = _google_specific_ask_marker in (
                    google.required_commander_checkpoints or ""
                )
                if not already_reopened_with_specific_ask:
                    # Never reopened with the specific ask yet — reopen it.
                    # Gated on the CHECKPOINT TEXT itself, not disposition,
                    # because answering a checkpoint (record_commander_answer)
                    # moves disposition to WATCHLIST, not back to
                    # PENDING_COMMANDER — checking disposition here would
                    # make this branch re-fire on every later run and wipe
                    # out a real answer with the same "need your info" ask.
                    acct.set_disposition(
                        session,
                        google.canonical_opportunity_id,
                        Disposition.pending_commander,
                        evidence=(
                            "Reopened: prior Commander answer approved proceeding but "
                            "did not supply the personal-data fields the intake form "
                            "requires."
                        ),
                        new_checkpoint=(
                            "To submit the Google Incognito privacy-lawsuit intake, we "
                            "need: (1) your full legal name, (2) an email or phone "
                            "number the law firm can reach you at, and (3) the "
                            "approximate date range you used Chrome Incognito/private "
                            "browsing while signed into a Google account. Hunter will "
                            "not submit the intake without these."
                        ),
                    )
                elif google.commander_response:
                    identity_fields = _available_identity_fields(["full_name", "email", "phone"])
                    if identity_fields.get("full_name") and (
                        identity_fields.get("email") or identity_fields.get("phone")
                    ):
                        task_svc.dispatch_task(
                            task_type="intake_form_submission",
                            spec_payload={
                                "intake_url": INTAKE_URL,
                                "canonical_opportunity_id": google.canonical_opportunity_id,
                                "identity_fields": identity_fields,
                            },
                            session=session,
                            source_type="canonical_opportunity",
                            source_id=google.canonical_opportunity_id,
                            priority=10,
                            idempotency_key=f"intake-submit:{google.canonical_opportunity_id}:{_date.today().isoformat()}",
                            max_attempts=2,
                        )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "hunter ledger startup bootstrap failed — %s: %s",
            type(exc).__name__,
            exc,
        )


def _bootstrap_commander_documents_after_startup() -> None:
    """Seed Commander's capability/experience profile as a
    CommanderDocument on first boot, from Hunter's own runtime — the same
    idempotent seed pattern as the ledger bootstrap above. Safe to run on
    every deploy/restart; a no-op once the document exists."""
    try:
        from app.database.config import engine
        from app.services import commander_documents as docs_svc

        with Session(engine) as session:
            seeded = docs_svc.seed_capability_profile(session)
            if seeded:
                _startup_logger.info(
                    "seeded Commander capability profile document_id=%s", seeded.document_id
                )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "commander documents startup bootstrap failed — %s: %s",
            type(exc).__name__,
            exc,
        )


def _bootstrap_resume_manual_action_tasks_after_startup() -> None:
    """Commander's to-do-list resume step: any canonical opportunity that
    hit something only a human can clear (e.g. a CAPTCHA) is parked as a
    PENDING_COMMANDER checkpoint tagged [MANUAL-ACTION-NEEDED] by
    tasks.escalate_task(). Once Commander answers it, this re-dispatches
    the same task for a fresh automated attempt. Runs on every
    boot/restart — a no-op once all answered checkpoints have already
    been resumed, since dispatch_task's idempotency key is keyed on the
    answer timestamp."""
    try:
        from app.database.config import engine
        from app.services import tasks as task_svc

        with Session(engine) as session:
            resumed = task_svc.resume_manual_action_tasks(session)
            if resumed:
                _startup_logger.info(
                    "resumed %d manual-action task(s) following Commander answers: %s",
                    len(resumed),
                    [t.source_id for t in resumed],
                )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "manual-action resume startup bootstrap failed — %s: %s",
            type(exc).__name__,
            exc,
        )


def _bootstrap_backfill_local_business_dispatch_after_startup() -> None:
    """EOD priority (Commander, 2026-09-10): "ensure the contact/routing
    corrections apply to existing eligible leads, not only future
    inserts." Today's fix (routing local_business_prospector sources to
    service_outreach instead of a phantom task_type) only takes effect
    at dispatch time — auto_dispatch_for_source is a pure function of a
    source's current origin_module/category — but dispatch itself only
    fires automatically for NEWLY ingested opportunities
    (process_new_opportunity, called on insert). Sources already in the
    database from before today's fix never got that call and would
    otherwise sit un-dispatched until their next full re-discovery.
    Re-dispatches all of them now; idempotent (dispatch_task's
    idempotency key on source+task_type returns the existing task if one
    is already active, and creates a fresh attempt if the prior one
    already failed/escalated — the exact case for these sources). Runs
    on every boot — a no-op once caught up, since a successfully
    dispatched source's next call just returns its existing task."""
    try:
        from sqlmodel import select as _select

        from app.database.config import engine
        from app.models.income_source import IncomeSource
        from app.services.tasks import auto_dispatch_for_source

        with Session(engine) as session:
            sources = session.exec(
                _select(IncomeSource).where(IncomeSource.origin_module == "local_business_prospector")
            ).all()
            dispatched = []
            for source in sources:
                task = auto_dispatch_for_source(source.source_id, session)
                if task:
                    dispatched.append((source.source_id, task.task_id))
            if dispatched:
                _startup_logger.info(
                    "backfill-dispatched %d existing local_business_prospector source(s): %s",
                    len(dispatched), dispatched,
                )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "local_business_prospector backfill-dispatch bootstrap failed — %s: %s",
            type(exc).__name__, exc,
        )


def _log_production_inventory_diagnostics() -> None:
    """
    Commander's Track 1 (2026-09-10): resolve the "authoritative production
    inventory" access gap. Preference order was (1) Render shell/SSH — not
    available in this engineering session (no ssh binary, and this
    sandbox's egress is HTTPS-proxy-only, confirmed by a direct test);
    (2) an existing authenticated admin inspection mechanism — none
    exists and this sandbox has no HTTPS egress to the live site either;
    so this is (3): a narrowly scoped, read-only diagnostic that runs
    through the application's own existing DB connection and reports
    only AGGREGATE COUNTS via the application log (never row content,
    descriptions, contact info, or credentials) — read via Render's log
    API, not a new HTTP endpoint (none is added).

    Deliberately placed on the WEB service's own bootstrap, not the
    worker's: render.yaml shows only the web service has the persistent
    disk (`disk: hunter-data` mounted at /data) and HUNTER_DB_PATH; the
    worker has no disk mount at all and only talks to the web service
    over HTTP — it cannot see this database, so nothing here assumes it
    can.
    """
    from sqlalchemy import func
    from sqlmodel import select

    from app.database.config import DATABASE_URL, engine
    from app.models.action_packet import ActionPacket
    from app.models.execution_outcome import ExecutionOutcome
    from app.models.hunter_ledger import CanonicalOpportunity, Disposition, ExecutionRecord
    from app.models.income_source import IncomeSource
    from app.models.provider_execution import ProviderExecution
    from app.models.task import Task

    try:
        db_path = DATABASE_URL.replace("sqlite:///", "", 1)
        _startup_logger.info(
            "INVENTORY_DIAG db_path=%s on_persistent_disk=%s exists=%s size_bytes=%s",
            db_path,
            db_path.startswith("/data/"),
            os.path.exists(db_path),
            os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        )

        with Session(engine) as session:
            opp_total = session.exec(select(func.count()).select_from(CanonicalOpportunity)).one()
            by_lane = dict(session.exec(select(CanonicalOpportunity.lane, func.count()).group_by(CanonicalOpportunity.lane)).all())
            by_disposition = dict(session.exec(select(CanonicalOpportunity.disposition, func.count()).group_by(CanonicalOpportunity.disposition)).all())
            by_provenance = dict(session.exec(select(CanonicalOpportunity.source_provenance, func.count()).group_by(CanonicalOpportunity.source_provenance)).all())
            _startup_logger.info(
                "INVENTORY_DIAG CanonicalOpportunity total=%d by_lane=%s by_disposition=%s by_provenance=%s",
                opp_total, by_lane, by_disposition, by_provenance,
            )

            eligible_states = [Disposition.pending_research.value, Disposition.screened_only.value, Disposition.watchlist.value]
            terminal_states = [Disposition.executed.value, Disposition.rejected.value, Disposition.duplicate.value, Disposition.inapplicable.value, Disposition.expired.value]
            blocked_states = [Disposition.blocked.value, Disposition.blocked_infrastructure.value, Disposition.blocked_capability.value]
            eligible_count = session.exec(select(func.count()).select_from(CanonicalOpportunity).where(CanonicalOpportunity.disposition.in_(eligible_states))).one()
            terminal_count = session.exec(select(func.count()).select_from(CanonicalOpportunity).where(CanonicalOpportunity.disposition.in_(terminal_states))).one()
            waiting_count = session.exec(select(func.count()).select_from(CanonicalOpportunity).where(CanonicalOpportunity.disposition == Disposition.pending_commander.value)).one()
            blocked_count = session.exec(select(func.count()).select_from(CanonicalOpportunity).where(CanonicalOpportunity.disposition.in_(blocked_states))).one()
            _startup_logger.info(
                "INVENTORY_DIAG CanonicalOpportunity eligible=%d waiting_commander=%d blocked=%d terminal=%d",
                eligible_count, waiting_count, blocked_count, terminal_count,
            )

            er_total = session.exec(select(func.count()).select_from(ExecutionRecord)).one()
            _startup_logger.info("INVENTORY_DIAG ExecutionRecord total=%d", er_total)

            src_total = session.exec(select(func.count()).select_from(IncomeSource)).one()
            by_origin = dict(session.exec(select(IncomeSource.origin_module, func.count()).group_by(IncomeSource.origin_module)).all())
            by_status = dict(session.exec(select(IncomeSource.status, func.count()).group_by(IncomeSource.status)).all())
            by_category = dict(session.exec(select(IncomeSource.category, func.count()).group_by(IncomeSource.category)).all())
            _startup_logger.info(
                "INVENTORY_DIAG IncomeSource total=%d by_origin_module=%s by_status=%s by_category=%s",
                src_total, by_origin, by_status, by_category,
            )

            task_total = session.exec(select(func.count()).select_from(Task)).one()
            by_task_type = dict(session.exec(select(Task.task_type, func.count()).group_by(Task.task_type)).all())
            by_task_status = dict(session.exec(select(Task.status, func.count()).group_by(Task.status)).all())
            _startup_logger.info(
                "INVENTORY_DIAG Task total=%d by_task_type=%s by_status=%s",
                task_total, by_task_type, by_task_status,
            )

            # Track 5 (Commander, 2026-09-10): historical impact of the
            # action_packet_id=None bug fixed in 70ce052. Every
            # ExecutionOutcome(...) construction was one transaction with
            # its ActionPacket's execution_state update — a NOT NULL
            # violation there rolled back the WHOLE transaction, so an
            # affected packet's execution_state would still show
            # active/in_progress (never advanced to completed/failed),
            # not "completed with a missing outcome." ProviderExecution
            # is the real broker-order-truth table, written independently
            # via the broker API call itself — unaffected by this bug.
            ap_total = session.exec(select(func.count()).select_from(ActionPacket)).one()
            ap_by_state = dict(session.exec(select(ActionPacket.execution_state, func.count()).group_by(ActionPacket.execution_state)).all())
            _startup_logger.info("INVENTORY_DIAG ActionPacket total=%d by_execution_state=%s", ap_total, ap_by_state)

            eo_total = session.exec(select(func.count()).select_from(ExecutionOutcome)).one()
            _startup_logger.info("INVENTORY_DIAG ExecutionOutcome total=%d", eo_total)

            pe_total = session.exec(select(func.count()).select_from(ProviderExecution)).one()
            pe_by_status = dict(session.exec(select(ProviderExecution.execution_status, func.count()).group_by(ProviderExecution.execution_status)).all())
            _startup_logger.info("INVENTORY_DIAG ProviderExecution total=%d by_execution_status=%s", pe_total, pe_by_status)

            # Packets with a real broker order (ProviderExecution exists)
            # whose own execution_state never reached a terminal value —
            # these are the ones potentially orphaned by the bug: broker
            # order occurred, but Hunter's own reconciliation kept failing
            # to record it, so it would be re-attempted on every future
            # reconciliation cycle rather than being corrupted or
            # double-counted (the rollback is all-or-nothing).
            provider_packet_ids = set(session.exec(select(ProviderExecution.packet_id)).all())
            stuck_states = {"active", "in_progress", "planned"}
            stuck_packet_ids = set(
                session.exec(
                    select(ActionPacket.id).where(ActionPacket.execution_state.in_(stuck_states))
                ).all()
            )
            orphaned = provider_packet_ids & stuck_packet_ids
            _startup_logger.info(
                "INVENTORY_DIAG reconciliation_check packets_with_broker_order=%d packets_stuck_non_terminal=%d "
                "potentially_orphaned_by_action_packet_id_bug=%d",
                len(provider_packet_ids), len(stuck_packet_ids), len(orphaned),
            )

            # Bridge check: CanonicalOpportunity and IncomeSource are
            # separate models with no FK between them anywhere in the
            # codebase — confirm that empirically rather than by code
            # reading alone. ID sets only, no other fields.
            opp_ids = set(session.exec(select(CanonicalOpportunity.canonical_opportunity_id)).all())
            src_ids = set(session.exec(select(IncomeSource.source_id)).all())
            _startup_logger.info(
                "INVENTORY_DIAG bridge_check canonical_opportunity_count=%d income_source_count=%d id_overlap=%d",
                len(opp_ids), len(src_ids), len(opp_ids & src_ids),
            )

            # Group real equities-trading failure reasons (Commander,
            # 2026-09-10: "group failure reasons and identify the first
            # broken shared component"). auto_place_trade_for_source()
            # persists WHY a packet never reached the broker directly on
            # ActionPacket.execution_notes via
            # _mark_packet_trade_skipped()/fail_packet_execution() — a
            # short, fixed set of Hunter-generated diagnostic strings
            # ("No funded allocation is available for trade submission",
            # "No trade symbol found...", etc.), not personal data.
            # Grouped by the first 60 chars (the fixed-reason prefix,
            # before any dynamic order-id suffix) so this stays a
            # redacted summary, not raw content.
            failure_notes = session.exec(
                select(ActionPacket.execution_notes).where(
                    ActionPacket.execution_state.in_(["failed", "canceled"]),
                    ActionPacket.execution_notes.is_not(None),
                )
            ).all()
            reason_counts: dict[str, int] = {}
            for note in failure_notes:
                key = (note or "")[:60]
                reason_counts[key] = reason_counts.get(key, 0) + 1
            _startup_logger.info(
                "INVENTORY_DIAG equities_failure_reasons total_with_notes=%d by_reason_prefix=%s",
                len(failure_notes), reason_counts,
            )

            # Track A item 4 (Commander, 2026-09-10): classify the 36
            # error-42210000 broker rejections. The 60-char prefix above
            # truncates the actual broker message, which is what's needed
            # to tell a valid risk/account restriction from an expected
            # market constraint from an invalid-request defect. Alpaca's
            # own error text (e.g. "insufficient buying power",
            # "asset X is not tradable", "opg orders only between...") is
            # Hunter's own broker-integration diagnostic content, not
            # personal data. Deduplicated (not one line per packet) and
            # capped so this stays a bounded, redacted sample rather than
            # a full record dump.
            broker_rejection_notes = {
                note for note in failure_notes
                if note and note.startswith('Trade skipped before broker submission: {"code":42210000')
            }
            _startup_logger.info(
                "INVENTORY_DIAG broker_rejection_42210000_distinct_messages count=%d messages=%s",
                len(broker_rejection_notes), sorted(broker_rejection_notes)[:15],
            )

            # Classify the historical "completed" tasks (Commander,
            # 2026-09-10): task_id/task_type/engine/source_id are Hunter's
            # own internal identifiers, not personal data. outcome_notes
            # is Hunter's own short generated summary string (e.g. "sent
            # real outreach email" / "prepared service outreach copy") —
            # truncated defensively, never full task spec/contact content.
            from app.models.task import Task, TaskAttempt, TaskStatus

            completed = session.exec(select(Task).where(Task.status == TaskStatus.completed)).all()
            for t in completed:
                last_attempt = session.exec(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_id == t.task_id)
                    .order_by(TaskAttempt.attempt_number.desc())
                ).first()
                _startup_logger.info(
                    "INVENTORY_DIAG completed_task task_id=%s task_type=%s source_id=%s engine=%s outcome_notes=%r",
                    t.task_id, t.task_type, t.source_id,
                    last_attempt.engine if last_attempt else None,
                    (t.outcome_notes or "")[:120],
                )

            # EOD safety verification (Commander, 2026-09-10): "an
            # uncertain or accepted-but-unrecorded order is reconciled
            # before any resubmission." "active" is the ActionPacket
            # state submit_packet_trade() sets immediately BEFORE calling
            # the broker (planned -> active -> in_progress happens right
            # before adapter.place_order()) — a packet stuck in "active"
            # (never advanced to in_progress/completed/failed) means
            # something interrupted submission at exactly that boundary.
            # id/source_id are Hunter's own identifiers; execution_notes
            # is Hunter's own short diagnostic string, truncated.
            active_packets_diag = session.exec(
                select(ActionPacket).where(ActionPacket.execution_state == "active")
            ).all()
            for p in active_packets_diag:
                _startup_logger.info(
                    "INVENTORY_DIAG active_packet packet_id=%s source_id=%s execution_updated_at=%s execution_notes=%r",
                    p.id, p.source_id, p.execution_updated_at,
                    (p.execution_notes or "")[:160],
                )

            # RECYCLE's OWN local tracking (Commander, 2026-09-10 — the
            # correction: broker history must be checked directly rather
            # than inferred from ActionPacket/ProviderExecution being
            # empty). recycle_engine.execute_entries() places real Alpaca
            # orders via get_alpaca_adapter().place_order() DIRECTLY — an
            # entirely separate path from auto_place_trade_for_source()'s
            # ActionPacket/ProviderExecution flow — and records its own
            # activity via position_lifecycle_svc.record_entry_submission
            # into PositionLifecycle, not those tables. This is very
            # likely where the real, broker-confirmed fills actually
            # live locally.
            from app.models.position_lifecycle import PositionLifecycle

            pl_total = session.exec(select(func.count()).select_from(PositionLifecycle)).one()
            pl_by_status = dict(session.exec(select(PositionLifecycle.status, func.count()).group_by(PositionLifecycle.status)).all())
            _startup_logger.info(
                "INVENTORY_DIAG PositionLifecycle total=%d by_status=%s",
                pl_total, pl_by_status,
            )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning("inventory diagnostics failed — %s: %s", type(exc).__name__, exc)

    # Separate try/except: broker connectivity failure must never mask or
    # abort the DB diagnostics above. Read-only Alpaca queries via the
    # existing, tested AlpacaAdapter (Commander, 2026-09-10: "use the
    # deployed application's existing broker connection for narrowly
    # scoped, read-only diagnostics. Log only redacted summaries.") — no
    # order placement, no account_id, no per-symbol/per-order detail;
    # aggregate counts and this account's own dollar figures only (not
    # personal data — Hunter already reports these via /budget and the
    # EOD report).
    try:
        from app.integration.brokerage.alpaca import get_alpaca_adapter

        adapter = get_alpaca_adapter()
        account = adapter.get_account()
        _startup_logger.info(
            "INVENTORY_DIAG broker_account status=%s currency=%s equity=%.2f cash=%.2f buying_power=%.2f",
            account.status, account.currency, account.portfolio_value, account.cash, account.buying_power,
        )

        positions = adapter.get_positions()
        total_market_value = sum(p.market_value or 0.0 for p in positions)
        _startup_logger.info(
            "INVENTORY_DIAG broker_positions open_count=%d total_market_value=%.2f",
            len(positions), total_market_value,
        )

        orders = adapter.list_orders(limit=100)
        by_status: dict[str, int] = {}
        for o in orders:
            by_status[o.status] = by_status.get(o.status, 0) + 1
        _startup_logger.info(
            "INVENTORY_DIAG broker_orders returned=%d by_status=%s (most-recent-first, limit=100)",
            len(orders), by_status,
        )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning("broker read-only diagnostic failed — %s: %s", type(exc).__name__, exc)


def _run_broker_history_reconciliation_once() -> None:
    """Gated by RUN_BROKER_HISTORY_RECONCILIATION_ON_STARTUP (default
    False) — not a permanent startup dependency. The durable, reusable
    mechanism is the admin-only POST /autotrader/broker-reconciliation
    endpoint; this only lets one deploy also run it once, for triggering
    it without direct access to call the endpoint. Meant to be turned
    back off (env var reset to false) after the run it was turned on
    for."""
    from app.database.config import engine as _engine
    from app.services.broker_history_reconciliation import reconcile_broker_history
    from sqlmodel import Session as _Session

    try:
        with _Session(_engine) as session:
            result = reconcile_broker_history(session)
        _startup_logger.info(
            "BROKER_HISTORY_RECONCILIATION_STARTUP_RUN marker=%s examined=%d matched_packet_based=%d "
            "matched_recycle=%d restored=%d unmatched=%d uncertain_packets=%d coverage=%s",
            result["reconciliation_marker"],
            result["broker_orders_examined"],
            result["matched_packet_based"],
            result["matched_recycle"],
            result["restored_count"],
            result["unmatched_count"],
            result["internal_packets_with_uncertain_outcomes"],
            result["coverage"],
        )
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning(
            "broker history reconciliation startup run failed — %s: %s", type(exc).__name__, exc
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keep the critical startup path local and bounded. External opportunity
    # intake is deliberately deferred so Render's health check can answer fast.
    try:
        create_db_and_tables()
    except Exception as exc:  # noqa: BLE001
        _startup_logger.warning("create_db_and_tables failed: %s", exc)

    # Recovery-board finding (Commander, 2026-09-10): APScheduler's default
    # interval-trigger first run is now+interval, so every redeploy resets
    # these jobs' countdown from zero — on a day with many deploys, due
    # work can be repeatedly postponed and never actually run.
    # next_run_time=now makes each process start (including every
    # redeploy) fire one immediate catch-up pass before settling into the
    # normal interval — a durable scheduler discovering and dispatching
    # existing eligible work, not an engineer manually selecting a
    # candidate. EOD finding (2026-09-10, later the same day): this fix
    # was applied to ledger_recovery_loop/checkpoint_resume/task_retry_sweep
    # but NOT to discovery_scan (3h interval) or signal_scan (6h interval)
    # — with ~9 redeploys today, discovery_scan in particular has likely
    # never completed a natural cycle, directly stalling new
    # local_business_prospector leads for the service-outreach path.
    from datetime import datetime as _datetime, timezone as _timezone
    _due_work_now = _datetime.now(_timezone.utc)
    scheduler.add_job(daily_scan_task, "cron", hour=9, minute=35, timezone=_SCHEDULER_TZ, id="daily_scan", misfire_grace_time=3600)
    scheduler.add_job(discovery_scan_task, "interval", seconds=DISCOVERY_SCAN_INTERVAL_SECONDS, id="discovery_scan", max_instances=1, misfire_grace_time=600, next_run_time=_due_work_now)
    scheduler.add_job(signal_scan_task, "interval", seconds=SIGNAL_SCAN_INTERVAL_SECONDS, id="signal_scan", max_instances=1, misfire_grace_time=900, next_run_time=_due_work_now)
    scheduler.add_job(weekly_report_task, "cron", day_of_week="mon", hour=8, minute=0, timezone=_SCHEDULER_TZ, id="weekly_report", misfire_grace_time=3600)
    scheduler.add_job(morning_report_task, "cron", hour=MORNING_REPORT_HOUR, minute=MORNING_REPORT_MINUTE, timezone=_SCHEDULER_TZ, id="morning_report", misfire_grace_time=1800)
    if ALPACA_ENABLED and STRATEGY_MODE == "RECYCLE":
        scheduler.add_job(recycle_cycle_task, "interval", seconds=RECYCLE_CYCLE_INTERVAL_SECONDS, id="recycle_cycle", max_instances=1, misfire_grace_time=30)
    scheduler.add_job(leon_daily_commerce_task, "cron", hour=8, minute=5, timezone=_SCHEDULER_TZ, id="leon_daily", misfire_grace_time=3600)
    scheduler.add_job(policy_scan_task, "cron", hour=6, minute=30, timezone=_SCHEDULER_TZ, id="policy_scan", misfire_grace_time=3600)
    scheduler.add_job(ledger_recovery_loop_task, "interval", seconds=LEDGER_LOOP_INTERVAL_SECONDS, id="ledger_recovery_loop", max_instances=1, misfire_grace_time=600, next_run_time=_due_work_now)
    scheduler.add_job(checkpoint_resume_task, "interval", seconds=CHECKPOINT_RESUME_INTERVAL_SECONDS, id="checkpoint_resume", max_instances=1, misfire_grace_time=300, next_run_time=_due_work_now)
    scheduler.add_job(task_retry_sweep_task, "interval", seconds=TASK_RETRY_SWEEP_INTERVAL_SECONDS, id="task_retry_sweep", max_instances=1, misfire_grace_time=300, next_run_time=_due_work_now)
    scheduler.start()

    # Do not block ASGI startup/health on opportunity intake or its providers.
    asyncio.create_task(asyncio.to_thread(_bootstrap_intake_after_startup))
    asyncio.create_task(asyncio.to_thread(_bootstrap_hunter_ledger_actions_after_startup))
    asyncio.create_task(asyncio.to_thread(_bootstrap_commander_documents_after_startup))
    asyncio.create_task(asyncio.to_thread(_bootstrap_resume_manual_action_tasks_after_startup))
    asyncio.create_task(asyncio.to_thread(_bootstrap_backfill_local_business_dispatch_after_startup))
    asyncio.create_task(asyncio.to_thread(_log_production_inventory_diagnostics))
    if RUN_BROKER_HISTORY_RECONCILIATION_ON_STARTUP:
        asyncio.create_task(asyncio.to_thread(_run_broker_history_reconciliation_once))
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Hunter",
    description="Elite Liberation Agent — autonomous income operations engine",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(system_router)
app.include_router(opportunities_router)
app.include_router(reports_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(budget_router)
app.include_router(autotrader_router)
app.include_router(alerts_router)
app.include_router(packets_router)
app.include_router(strategies_router)
app.include_router(operations_router)
app.include_router(sources_router)
app.include_router(execution_router)
app.include_router(performance_router)
app.include_router(advisors_router)
app.include_router(monitoring_router)
app.include_router(handoff_router)
app.include_router(leads_router)
app.include_router(decisions_router)
app.include_router(marketplace_router)
app.include_router(tasks_router)
app.include_router(auth_router)
app.include_router(diag_router)
app.include_router(signals_router)
app.include_router(forge_router)
app.include_router(quickcash_router)
app.include_router(store_router)
app.include_router(assistant_router)
app.include_router(policy_router)
app.include_router(hunter_ledger_router)
app.include_router(commander_documents_router)

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")
    app.mount("/media", StaticFiles(directory=str(_FRONTEND_DIST / "media")), name="media")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    def root():
        return {"message": "Hunter v0.2.0 — autonomous operations engine running"}


class _StripApiPrefix:
    __slots__ = ("_app",)

    def __init__(self, inner_app):
        self._app = inner_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            if path.startswith("/api"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                raw: bytes = scope.get("raw_path", b"")
                if raw.startswith(b"/api"):
                    scope["raw_path"] = raw[4:] or b"/"
        await self._app(scope, receive, send)


app = _StripApiPrefix(app)
