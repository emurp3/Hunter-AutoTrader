"""
AutoTrader intake service with live-source health checks and seed fallback.

Hunter prefers the real AutoTrader export bridge (`data/autotrader.json`), but
it must stay operational even while AutoTrader is offline. This service:

1. checks whether the live export is missing, empty, stale, invalid, or ready
2. falls back to `data/seed_opportunities.json` when live data is unusable
3. records enough state for the Operations dashboard to show whether Hunter is
   running on live AutoTrader data or a temporary fallback source
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.adapters.base import AutoTraderAdapter
from app.adapters.file_adapter import AutoTraderSourceError, RealFileAdapter
from app.adapters.http_stub import HttpAdapter
from app.models.income_source import IncomeSource, SourceStatus
from app.services.scoring import score_opportunity

logger = logging.getLogger(__name__)

LIVE_MODULE_NAME = "autotrader"
SEED_MODULE_NAME = "autotrader_seed"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTOTRADER_FILE_PATH = BACKEND_ROOT / "data" / "autotrader.json"
DEFAULT_SEED_FILE_PATH = BACKEND_ROOT / "data" / "seed_opportunities.json"
DEFAULT_STALE_HOURS = 24


class AutoTraderConfigError(RuntimeError):
    """Raised when AutoTrader source configuration is missing or invalid."""


@dataclass
class IntakeResult:
    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None
    source_mode: str = "offline"  # live | seed | offline
    fallback_used: bool = False
    fallback_reason: str | None = None
    live_data_status: str | None = None
    live_data_message: str | None = None
    records_loaded: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "error_details": self.error_details,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "source_mode": self.source_mode,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "live_data_status": self.live_data_status,
            "live_data_message": self.live_data_message,
            "records_loaded": self.records_loaded,
        }


@dataclass
class _IntakeState:
    last_scan_at: datetime | None = None
    last_source_type: str | None = None
    last_status: str = "never_run"  # never_run | success | fallback | aborted | error
    last_scanned: int = 0
    last_inserted: int = 0
    last_updated: int = 0
    last_skipped: int = 0
    last_errors: int = 0
    last_error: str | None = None
    source_configured: bool = True
    source_reachable: bool | None = None
    live_data_status: str = "missing"  # ready | missing | empty | stale | invalid | unreachable
    live_data_message: str = "AutoTrader offline / no live data."
    live_data_path: str | None = str(DEFAULT_AUTOTRADER_FILE_PATH)
    live_data_updated_at: str | None = None
    live_data_record_count: int = 0
    stale_after_hours: int = DEFAULT_STALE_HOURS
    using_fallback: bool = False
    fallback_reason: str | None = None
    fallback_path: str | None = str(DEFAULT_SEED_FILE_PATH)
    fallback_record_count: int = 0
    current_data_mode: str = "offline"  # live | seed | offline


@dataclass
class SourceSnapshot:
    source_type: str
    status: str
    message: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    path: str | None = None
    updated_at: str | None = None
    age_seconds: int | None = None
    record_count: int = 0


_state = _IntakeState()


def get_intake_state() -> _IntakeState:
    return _state


def _configured_source_type() -> str:
    # Re-read from .env files on every call so changes take effect without restart.
    # dotenv_values() reads the file but does not modify os.environ.
    from dotenv import dotenv_values as _dv
    for _p in (BACKEND_ROOT / ".env", BACKEND_ROOT.parent / "backend.env"):
        if _p.exists():
            _val = _dv(str(_p)).get("AUTOTRADER_SOURCE_TYPE", "").strip().lower()
            if _val:
                return _val
    return os.getenv("AUTOTRADER_SOURCE_TYPE", "").strip().lower() or ""


def _configured_live_path() -> Path:
    raw = os.getenv("AUTOTRADER_FILE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_AUTOTRADER_FILE_PATH


def _configured_seed_path() -> Path:
    raw = os.getenv("HUNTER_SEED_OPPORTUNITIES_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_SEED_FILE_PATH


def _stale_after_hours() -> int:
    raw = os.getenv("AUTOTRADER_STALE_HOURS", "").strip()
    if not raw:
        return DEFAULT_STALE_HOURS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_STALE_HOURS


def get_adapter() -> AutoTraderAdapter:
    source_type = _configured_source_type()

    if source_type == "file":
        return RealFileAdapter(path=str(_configured_live_path()))

    if source_type == "http":
        return HttpAdapter(
            base_url=os.getenv("AUTOTRADER_HTTP_URL"),
            api_key=os.getenv("AUTOTRADER_HTTP_API_KEY"),
        )

    if source_type == "live":
        raise AutoTraderConfigError("live mode: use run_intake() directly — no adapter required.")

    raise AutoTraderConfigError(
        f"AUTOTRADER_SOURCE_TYPE is '{source_type!r}'. "
        "Set it to 'live', 'file', or 'http'."
    )


def _read_json_array(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise AutoTraderSourceError(f"{label} file not found: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AutoTraderSourceError(f"{label} file could not be read: {path} — {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AutoTraderSourceError(f"{label} file contains invalid JSON: {path} — {exc}") from exc

    if not isinstance(data, list):
        raise AutoTraderSourceError(
            f"{label} file must contain a JSON array. Got {type(data).__name__}."
        )

    if any(not isinstance(item, dict) for item in data):
        raise AutoTraderSourceError(f"{label} file must contain only JSON objects.")

    return data


def assess_live_source() -> SourceSnapshot:
    source_type = _configured_source_type()
    stale_hours = _stale_after_hours()

    if source_type == "file":
        path = _configured_live_path()
        if not path.exists():
            return SourceSnapshot(
                source_type="file",
                status="missing",
                message="AutoTrader offline / no live data. Export file is missing.",
                path=str(path),
            )

        try:
            findings = _read_json_array(path, label="AutoTrader export")
        except AutoTraderSourceError as exc:
            return SourceSnapshot(
                source_type="file",
                status="invalid",
                message=f"AutoTrader offline / no live data. {exc}",
                path=str(path),
            )

        record_count = len(findings)
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age_seconds = int((datetime.now(timezone.utc) - modified_at).total_seconds())

        if record_count == 0:
            return SourceSnapshot(
                source_type="file",
                status="empty",
                message="AutoTrader offline / no live data. Export file is empty.",
                findings=findings,
                path=str(path),
                updated_at=modified_at.isoformat(),
                age_seconds=age_seconds,
                record_count=0,
            )

        if age_seconds > stale_hours * 3600:
            return SourceSnapshot(
                source_type="file",
                status="stale",
                message=f"AutoTrader offline / no live data. Export is stale ({age_seconds // 3600}h old).",
                findings=findings,
                path=str(path),
                updated_at=modified_at.isoformat(),
                age_seconds=age_seconds,
                record_count=record_count,
            )

        return SourceSnapshot(
            source_type="file",
            status="ready",
            message="AutoTrader live data is healthy.",
            findings=findings,
            path=str(path),
            updated_at=modified_at.isoformat(),
            age_seconds=age_seconds,
            record_count=record_count,
        )

    if source_type == "http":
        try:
            findings = get_adapter().fetch_findings()
        except AutoTraderSourceError as exc:
            return SourceSnapshot(
                source_type="http",
                status="unreachable",
                message=f"AutoTrader offline / no live data. {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return SourceSnapshot(
                source_type="http",
                status="invalid",
                message=f"AutoTrader offline / no live data. {exc}",
            )

        if not findings:
            return SourceSnapshot(
                source_type="http",
                status="empty",
                message="AutoTrader offline / no live data. HTTP source returned no findings.",
                findings=[],
                record_count=0,
            )

        return SourceSnapshot(
            source_type="http",
            status="ready",
            message="AutoTrader live data is healthy.",
            findings=findings,
            updated_at=datetime.now(timezone.utc).isoformat(),
            record_count=len(findings),
        )

    if source_type == "live":
        # live mode health check: verify at least one source adapter is enabled
        from app.config import (
            SOURCES_GIG_ENABLED, SOURCES_GITHUB_ENABLED, SOURCES_MARKETPLACE_ENABLED,
            SOURCES_SOCIAL_ENABLED, SOURCES_LOCAL_ENABLED, SOURCES_DIGITAL_ENABLED,
        )
        any_enabled = any([
            SOURCES_GIG_ENABLED, SOURCES_GITHUB_ENABLED, SOURCES_MARKETPLACE_ENABLED,
            SOURCES_SOCIAL_ENABLED, SOURCES_LOCAL_ENABLED, SOURCES_DIGITAL_ENABLED,
        ])
        if not any_enabled:
            return SourceSnapshot(
                source_type="live",
                status="invalid",
                message="AutoTrader live: all source adapters are disabled.",
            )
        return SourceSnapshot(
            source_type="live",
            status="ready",
            message="AutoTrader live source acquisition pipeline is active.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    if not source_type:
        return SourceSnapshot(
            source_type="none",
            status="missing",
            message="AUTOTRADER_SOURCE_TYPE is not set. Set it to 'live', 'file', or 'http'.",
        )

    return SourceSnapshot(
        source_type=source_type,
        status="invalid",
        message=f"AutoTrader offline / no live data. Unsupported source type '{source_type}'.",
    )


def load_seed_findings() -> tuple[list[dict[str, Any]], str]:
    path = _configured_seed_path()
    findings = _read_json_array(path, label="Hunter seed opportunities")
    if not findings:
        raise AutoTraderSourceError(f"Hunter seed opportunities file is empty: {path}")
    return findings, str(path)


def normalize_finding(raw: dict[str, Any], *, source_prefix: str) -> dict[str, Any] | None:
    raw_id = raw.get("id")
    if not raw_id:
        logger.warning("normalize_finding: skipping finding with missing 'id': %s", raw)
        return None

    description = str(raw.get("description", "")).strip()
    if not description:
        logger.warning("normalize_finding: skipping finding %s with empty description", raw_id)
        return None

    # date_found = date Hunter ingested the source, not the raw publication timestamp.
    # Using today ensures source discovery quota reflects when Hunter found the source.
    date_found = date.today()

    estimated_profit = raw.get("estimated_profit", raw.get("estimated_monthly_return", 0.0))
    try:
        estimated_profit = max(0.0, float(estimated_profit))
    except (TypeError, ValueError):
        estimated_profit = 0.0

    confidence_raw = raw.get("confidence")
    confidence: float | None = None
    if confidence_raw is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = None

    return {
        "source_id": f"{source_prefix}:{raw_id}",
        "description": description,
        "estimated_profit": estimated_profit,
        "currency": raw.get("currency", "USD"),
        "status": SourceStatus.new,
        "date_found": date_found,
        "next_action": raw.get("next_action") or raw.get("suggested_action"),
        "notes": raw.get("notes"),
        "category": raw.get("category"),
        "confidence": confidence,
    }


_UPDATE_FIELDS = ("estimated_profit", "next_action", "notes", "confidence", "category", "date_found")


def ingest_findings(session: Session, findings: list[dict[str, Any]], *, origin_module: str) -> IntakeResult:
    result = IntakeResult(scanned=len(findings))
    source_prefix = "at" if origin_module == LIVE_MODULE_NAME else "seed"

    existing: dict[str, IncomeSource] = {
        record.source_id: record
        for record in session.exec(
            select(IncomeSource).where(IncomeSource.origin_module == origin_module)
        ).all()
    }

    for raw in findings:
        try:
            normalized = normalize_finding(raw, source_prefix=source_prefix)
            if normalized is None:
                result.errors += 1
                result.error_details.append(f"normalization failed: {raw.get('id', '?')}")
                continue

            sid = normalized["source_id"]

            if sid in existing:
                record = existing[sid]
                changed = False
                for field_name in _UPDATE_FIELDS:
                    if getattr(record, field_name) != normalized.get(field_name):
                        setattr(record, field_name, normalized.get(field_name))
                        changed = True
                if changed:
                    sr = score_opportunity(record, session)
                    record.score = sr.score
                    record.priority_band = sr.priority_band
                    record.score_rationale = sr.rationale
                    session.add(record)
                    result.updated += 1
                else:
                    result.skipped += 1
            else:
                record = IncomeSource(**normalized, origin_module=origin_module)
                sr = score_opportunity(record, session)
                record.score = sr.score
                record.priority_band = sr.priority_band
                record.score_rationale = sr.rationale
                session.add(record)
                result.inserted += 1

        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            detail = f"{raw.get('id', '?')}: {exc}"
            result.error_details.append(detail)
            logger.error("ingest: error processing finding — %s", detail)

    session.commit()

    if result.inserted > 0:
        from app.services.orchestrator import process_new_opportunity

        newly_inserted = session.exec(
            select(IncomeSource).where(
                IncomeSource.origin_module == origin_module,
                IncomeSource.status == SourceStatus.new,
            )
        ).all()
        for record in newly_inserted:
            try:
                process_new_opportunity(record, session)
            except Exception as exc:  # noqa: BLE001
                logger.error("ingest: orchestrator error for %s — %s", record.source_id, exc)

    return result


def _run_live_intake(session: Session) -> IntakeResult:
    """Bridge AutoTrader intake to the Hunter source acquisition pipeline."""
    from app.services.source_acquisition import run_source_acquisition

    _state.last_scan_at = datetime.now(timezone.utc)
    _state.last_source_type = "live"
    _state.source_configured = True

    try:
        acq = run_source_acquisition(session)
        errors = acq.get("errors", [])

        result = IntakeResult(
            scanned=acq.get("found", 0),
            inserted=acq.get("inserted", 0),
            updated=acq.get("updated", 0),
            skipped=acq.get("skipped", 0),
            errors=len(errors),
            error_details=errors,
            source_mode="live",
            fallback_used=False,
            live_data_status="ready",
            live_data_message="Live source acquisition completed.",
            records_loaded=acq.get("found", 0),
        )

        _state.source_reachable = True

        # If live acquisition found nothing, fall back to seed so quotas stay healthy.
        if result.inserted == 0 and result.updated == 0:
            logger.info("_run_live_intake: live acquisition returned 0 results — activating seed fallback")
            try:
                seed_findings, seed_path = load_seed_findings()
                seed_result = ingest_findings(session, seed_findings, origin_module=SEED_MODULE_NAME)
                seed_result.source_mode = "seed"
                seed_result.fallback_used = True
                seed_result.fallback_reason = "Live acquisition returned 0 results."
                seed_result.live_data_status = "ready"
                seed_result.live_data_message = "Live acquisition active but empty — seed fallback engaged."
                seed_result.records_loaded = len(seed_findings)
                _state.using_fallback = True
                _state.fallback_reason = "Live acquisition returned 0 results."
                _state.fallback_path = seed_path
                _state.fallback_record_count = len(seed_findings)
                _state.current_data_mode = "seed"
                _state.last_status = "fallback"
                _state.last_scanned = seed_result.scanned
                _state.last_inserted = seed_result.inserted
                _state.last_updated = seed_result.updated
                _state.last_skipped = seed_result.skipped
                _state.last_errors = seed_result.errors
                _state.last_error = None
                return seed_result
            except AutoTraderSourceError as seed_exc:
                logger.warning("_run_live_intake: seed fallback also failed — %s", seed_exc)

        _state.live_data_message = f"Live source acquisition: {result.inserted} new, {result.updated} updated."
        _state.using_fallback = False
        _state.current_data_mode = "live"
        _state.last_status = "partial" if errors else "success"
        _state.last_scanned = result.scanned
        _state.last_inserted = result.inserted
        _state.last_updated = result.updated
        _state.last_skipped = result.skipped
        _state.last_errors = result.errors
        _state.last_error = errors[0] if errors else None

        return result

    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        logger.error("_run_live_intake failed: %s", msg)
        # Hard failure — attempt seed fallback before giving up
        try:
            seed_findings, seed_path = load_seed_findings()
            seed_result = ingest_findings(session, seed_findings, origin_module=SEED_MODULE_NAME)
            seed_result.source_mode = "seed"
            seed_result.fallback_used = True
            seed_result.fallback_reason = msg
            seed_result.live_data_status = "error"
            seed_result.live_data_message = f"Live acquisition failed — seed fallback engaged. Error: {msg}"
            seed_result.records_loaded = len(seed_findings)
            _state.live_data_status = "error"
            _state.live_data_message = seed_result.live_data_message
            _state.source_reachable = False
            _state.using_fallback = True
            _state.fallback_reason = msg
            _state.fallback_path = seed_path
            _state.fallback_record_count = len(seed_findings)
            _state.current_data_mode = "seed"
            _state.last_status = "fallback"
            _state.last_scanned = seed_result.scanned
            _state.last_inserted = seed_result.inserted
            _state.last_updated = seed_result.updated
            _state.last_skipped = seed_result.skipped
            _state.last_errors = seed_result.errors
            _state.last_error = msg
            return seed_result
        except AutoTraderSourceError:
            pass

        _state.live_data_status = "error"
        _state.live_data_message = msg
        _state.source_reachable = False
        _state.current_data_mode = "offline"
        _state.last_status = "error"
        _state.last_error = msg

        return IntakeResult(
            aborted=True,
            abort_reason="source_acquisition_failed",
            error_details=[msg],
            source_mode="offline",
            live_data_status="error",
            live_data_message=msg,
        )


def run_intake(session: Session) -> IntakeResult:
    if _configured_source_type() == "live":
        return _run_live_intake(session)

    live_snapshot = assess_live_source()
    _state.last_scan_at = datetime.now(timezone.utc)
    _state.last_source_type = live_snapshot.source_type
    _state.source_configured = live_snapshot.source_type in ("file", "http", "live")
    _state.live_data_status = live_snapshot.status
    _state.live_data_message = live_snapshot.message
    _state.live_data_path = live_snapshot.path
    _state.live_data_updated_at = live_snapshot.updated_at
    _state.live_data_record_count = live_snapshot.record_count
    _state.stale_after_hours = _stale_after_hours()
    _state.source_reachable = live_snapshot.status == "ready"

    if live_snapshot.status == "ready":
        result = ingest_findings(session, live_snapshot.findings, origin_module=LIVE_MODULE_NAME)
        result.source_mode = "live"
        result.fallback_used = False
        result.live_data_status = live_snapshot.status
        result.live_data_message = live_snapshot.message
        result.records_loaded = live_snapshot.record_count
        _state.using_fallback = False
        _state.fallback_reason = None
        _state.fallback_path = str(_configured_seed_path())
        _state.current_data_mode = "live"
        _state.fallback_record_count = 0
        _state.last_status = "error" if result.errors and not (result.inserted or result.updated) else "success"
        _state.last_error = result.error_details[0] if result.error_details else None
    else:
        try:
            seed_findings, seed_path = load_seed_findings()
        except AutoTraderSourceError as exc:
            message = f"{live_snapshot.message} Seed fallback unavailable: {exc}"
            logger.error("run_intake: offline — %s", message)
            _state.using_fallback = False
            _state.fallback_reason = str(exc)
            _state.fallback_path = str(_configured_seed_path())
            _state.fallback_record_count = 0
            _state.current_data_mode = "offline"
            _state.last_status = "aborted"
            _state.last_error = message
            return IntakeResult(
                aborted=True,
                abort_reason="offline",
                error_details=[message],
                source_mode="offline",
                fallback_used=False,
                live_data_status=live_snapshot.status,
                live_data_message=live_snapshot.message,
            )

        result = ingest_findings(session, seed_findings, origin_module=SEED_MODULE_NAME)
        result.source_mode = "seed"
        result.fallback_used = True
        result.fallback_reason = live_snapshot.message
        result.live_data_status = live_snapshot.status
        result.live_data_message = live_snapshot.message
        result.records_loaded = len(seed_findings)
        _state.using_fallback = True
        _state.fallback_reason = live_snapshot.message
        _state.fallback_path = seed_path
        _state.fallback_record_count = len(seed_findings)
        _state.current_data_mode = "seed"
        _state.last_status = "fallback"
        _state.last_error = live_snapshot.message

    _state.last_scanned = result.scanned
    _state.last_inserted = result.inserted
    _state.last_updated = result.updated
    _state.last_skipped = result.skipped
    _state.last_errors = result.errors

    # ── Auto-promote strategies after any successful intake ──────────────────
    if not result.aborted and (result.inserted > 0 or result.updated > 0):
        try:
            from app.services.strategies import auto_promote_candidates
            promoted = auto_promote_candidates(session)
            if promoted:
                logger.info("auto_promote: promoted %d candidates to active after intake", len(promoted))
        except Exception as _exc:  # noqa: BLE001
            logger.warning("auto_promote: post-intake promotion failed — %s", _exc)

    # ── Creation lane — auto-trigger when live intake found nothing new ───────
    if not result.aborted and result.inserted == 0 and result.source_mode == "live":
        try:
            from app.services.creation import run_creation_lane
            _cr = run_creation_lane(session, trigger_reason="intake_dry")
            logger.info(
                "creation_lane: auto-triggered (intake_dry) — created=%d skipped=%d",
                _cr.created, _cr.skipped,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("creation_lane: auto-trigger failed — %s", _exc)

    return result
