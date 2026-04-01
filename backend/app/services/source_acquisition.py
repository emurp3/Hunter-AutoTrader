from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.config import (
    SOURCES_AFFILIATE_ENABLED,
    SOURCES_DIGITAL_ENABLED,
    SOURCES_GIG_ENABLED,
    SOURCES_GITHUB_ENABLED,
    SOURCES_LOCAL_ENABLED,
    SOURCES_MARKETPLACE_ENABLED,
    SOURCES_MAX_RESULTS_PER_RUN,
    SOURCES_RFP_ENABLED,
    SOURCES_SOCIAL_ENABLED,
)
from app.models.alert import AlertType, AlertPriority
from app.models.event import EventType
from app.models.income_source import IncomeSource, SourceStatus
from app.services import alerts as alert_svc
from app.services import events as event_svc
from app.services.orchestrator import process_new_opportunity, build_action_plan
from app.services.scoring import score_opportunity
from app.services.sources import (
    AffiliateScannerAdapter,
    DigitalProductGapAdapter,
    GigScannerAdapter,
    GitHubScannerAdapter,
    LocalBusinessProspectorAdapter,
    MarketplaceScannerAdapter,
    RfpScannerAdapter,
    SocialListenerAdapter,
    SourceOpportunity,
)

SOURCE_ORIGINS = (
    "social_listener",
    "gig_scanner",
    "github_scanner",
    "marketplace_scanner",
    "local_business_prospector",
    "digital_product_scanner",
    "rfp_scanner",
    "affiliate_scanner",
)


@dataclass
class AcquisitionState:
    last_run_at: str | None = None
    last_status: str = "never_run"  # never_run | success | partial | error
    last_error: str | None = None
    total_found: int = 0
    total_inserted: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    latest_results: list[dict[str, Any]] = field(default_factory=list)
    adapter_health: dict[str, dict[str, Any]] = field(default_factory=dict)


_state = AcquisitionState()


def _build_adapters():
    return [
        SocialListenerAdapter(enabled=SOURCES_SOCIAL_ENABLED),
        GigScannerAdapter(enabled=SOURCES_GIG_ENABLED),
        GitHubScannerAdapter(enabled=SOURCES_GITHUB_ENABLED),
        MarketplaceScannerAdapter(enabled=SOURCES_MARKETPLACE_ENABLED),
        LocalBusinessProspectorAdapter(enabled=SOURCES_LOCAL_ENABLED),
        DigitalProductGapAdapter(enabled=SOURCES_DIGITAL_ENABLED),
        RfpScannerAdapter(enabled=SOURCES_RFP_ENABLED),
        AffiliateScannerAdapter(enabled=SOURCES_AFFILIATE_ENABLED),
    ]


def get_source_status() -> dict[str, Any]:
    if not _state.adapter_health:
        for adapter in _build_adapters():
            _state.adapter_health[adapter.source_name()] = adapter.health_status()

    return {
        "last_run_at": _state.last_run_at,
        "last_status": _state.last_status,
        "last_error": _state.last_error,
        "totals": {
            "found": _state.total_found,
            "inserted": _state.total_inserted,
            "updated": _state.total_updated,
            "skipped": _state.total_skipped,
        },
        "sources": _state.adapter_health,
        "top_by_source": _top_by_source(_state.latest_results),
    }


def get_latest_results(origin_module: str | None = None) -> list[dict[str, Any]]:
    if origin_module:
        return [item for item in _state.latest_results if item["origin_module"] == origin_module]
    return _state.latest_results


def run_source_acquisition(session: Session) -> dict[str, Any]:
    adapters = _build_adapters()
    all_results: list[SourceOpportunity] = []
    health_map: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for adapter in adapters:
        try:
            results = adapter.run()
            all_results.extend(results)
            health_map[adapter.source_name()] = adapter.health_status()
        except Exception as exc:  # noqa: BLE001
            message = f"{adapter.source_name()} failed: {exc}"
            errors.append(message)
            alert_svc.raise_alert(
                alert_type=AlertType.source_failure,
                title=f"Source acquisition failure — {adapter.source_name()}",
                body=message,
                session=session,
                priority=AlertPriority.medium,
            )
            health = adapter.health_status()
            health.update({"status": "error", "live": False, "last_error": str(exc)})
            health_map[adapter.source_name()] = health

    deduped = _dedupe_results(all_results)[:SOURCES_MAX_RESULTS_PER_RUN]
    persist_summary = _persist_results(session, deduped)

    _state.last_run_at = datetime.now(timezone.utc).isoformat()
    _state.last_status = "partial" if errors else "success"
    _state.last_error = errors[0] if errors else None
    _state.total_found = len(deduped)
    _state.total_inserted = persist_summary["inserted"]
    _state.total_updated = persist_summary["updated"]
    _state.total_skipped = persist_summary["skipped"]
    _state.latest_results = [item.to_dict() for item in deduped]
    _state.adapter_health = health_map

    return {
        "last_run_at": _state.last_run_at,
        "status": _state.last_status,
        "errors": errors,
        "found": len(deduped),
        "inserted": persist_summary["inserted"],
        "updated": persist_summary["updated"],
        "skipped": persist_summary["skipped"],
        "top_by_source": _top_by_source(_state.latest_results),
        "sources": health_map,
    }


def _dedupe_results(results: list[SourceOpportunity]) -> list[SourceOpportunity]:
    ranked = sorted(results, key=lambda value: value.confidence, reverse=True)
    deduped: list[SourceOpportunity] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for item in ranked:
        if item.source_id in seen_ids:
            continue
        if item.source_url in seen_urls:
            continue
        seen_ids.add(item.source_id)
        seen_urls.add(item.source_url)
        deduped.append(item)

    guaranteed: list[SourceOpportunity] = []
    remaining: list[SourceOpportunity] = []
    per_origin_limit = 4
    per_origin_counts: dict[str, int] = {}

    for item in deduped:
        count = per_origin_counts.get(item.origin_module, 0)
        if count < per_origin_limit:
            guaranteed.append(item)
            per_origin_counts[item.origin_module] = count + 1
        else:
            remaining.append(item)

    selected = guaranteed[:SOURCES_MAX_RESULTS_PER_RUN]
    if len(selected) < SOURCES_MAX_RESULTS_PER_RUN:
        selected.extend(remaining[: SOURCES_MAX_RESULTS_PER_RUN - len(selected)])
    return selected


def _persist_results(session: Session, results: list[SourceOpportunity]) -> dict[str, int]:
    inserted = 0
    updated = 0
    skipped = 0

    for item in results:
        record = session.exec(
            select(IncomeSource).where(IncomeSource.source_id == item.source_id)
        ).first()

        notes = f"Source URL: {item.source_url}"
        notes += f" | Lane: {item.lane}"
        if item.signal_type:
            notes += f" | Signal: {item.signal_type}"

        if record:
            changed = False
            updates = {
                "description": item.description,
                "estimated_profit": item.estimated_profit,
                "currency": item.currency,
                "next_action": item.next_action,
                "notes": notes,
                "origin_module": item.origin_module,
                "category": item.category,
                "confidence": item.confidence,
            }
            for key, value in updates.items():
                if getattr(record, key) != value:
                    setattr(record, key, value)
                    changed = True

            if changed:
                sr = score_opportunity(record, session)
                record.score = sr.score
                record.priority_band = sr.priority_band
                record.score_rationale = sr.rationale
                session.add(record)
                session.commit()
                session.refresh(record)
                updated += 1
                event_svc.log_event(
                    record.source_id,
                    EventType.ingested,
                    session,
                    summary=f"{item.origin_module} refreshed opportunity",
                    metadata={"source_url": item.source_url},
                )
                if sr.priority_band in ("elite", "high"):
                    build_action_plan(record.source_id, session)
            else:
                skipped += 1
            continue

        record = IncomeSource(
            source_id=item.source_id,
            description=item.description,
            estimated_profit=item.estimated_profit,
            currency=item.currency,
            status=SourceStatus.new,
            date_found=datetime.fromisoformat(item.timestamp.replace("Z", "+00:00")).date(),
            next_action=item.next_action,
            notes=notes,
            origin_module=item.origin_module,
            category=item.category,
            confidence=item.confidence,
        )
        sr = score_opportunity(record, session)
        record.score = sr.score
        record.priority_band = sr.priority_band
        record.score_rationale = sr.rationale
        session.add(record)
        session.commit()
        session.refresh(record)
        inserted += 1

        event_svc.log_event(
            record.source_id,
            EventType.ingested,
            session,
            new_state=SourceStatus.new,
            summary=f"{item.origin_module} acquired a live opportunity",
            metadata={"source_url": item.source_url, "source_name": item.source_name},
        )

        if sr.priority_band in ("elite", "high"):
            process_new_opportunity(record, session)

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def _top_by_source(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(item["origin_module"], []).append(item)

    return {
        origin: sorted(items, key=lambda value: value["confidence"], reverse=True)[:5]
        for origin, items in grouped.items()
    }
