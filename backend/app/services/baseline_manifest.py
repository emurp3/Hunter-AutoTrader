"""
Baseline manifest freeze/import (Claude Hunter Implementation Addendum,
section "Canonical baseline manifest").

The 149-post `systemcracker_1` baseline is never renumbered — once a
`baseline_index` is frozen here, its identity fields (index, source
profile, canonical URL, title/claim, captured_at) are immutable. Later
posts go into the incremental queue via `queue_incremental_post`, which
continues the index sequence rather than reusing or reordering it.

This module only freezes/imports whatever entries it is given — it does
not fabricate baseline content. The real 149-item list from
`Hunter_Opportunity_Queue_2026-09-08.md` must be supplied (as a list of
dicts matching `REQUIRED_FIELDS`, e.g. via a JSON file) before the
baseline is materially populated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, select

from app.models.hunter_ledger import BaselineManifestEntry

REQUIRED_FIELDS = {
    "baseline_index",
    "source_profile",
    "canonical_source_url",
    "source_title_or_claim",
}

# Identity fields that may never change once a baseline_index is frozen.
_IMMUTABLE_FIELDS = {
    "baseline_index",
    "source_profile",
    "canonical_source_url",
    "source_title_or_claim",
    "incremental_after_baseline",
}

_FIRST_INCREMENTAL_INDEX = 150  # first slot after the frozen 1..149 baseline


class ManifestIntegrityError(ValueError):
    """Raised when an import would renumber or mutate a frozen entry's
    identity fields."""


def freeze_baseline_manifest(
    session: Session, entries: Iterable[dict]
) -> list[BaselineManifestEntry]:
    """Idempotently freeze a batch of manifest entries. Re-importing an
    existing baseline_index only updates mutable fields (screening_status,
    content_available, canonical_opportunity_id, duplicate_of,
    source_evidence_reference) — identity fields are compared and any
    mismatch raises rather than silently renumbering the baseline."""
    result: list[BaselineManifestEntry] = []

    for raw in entries:
        missing = REQUIRED_FIELDS - raw.keys()
        if missing:
            raise ManifestIntegrityError(
                f"manifest entry missing required fields: {sorted(missing)}"
            )

        existing = session.exec(
            select(BaselineManifestEntry).where(
                BaselineManifestEntry.baseline_index == raw["baseline_index"]
            )
        ).first()

        if existing:
            for field_name in _IMMUTABLE_FIELDS:
                if field_name in raw and str(raw[field_name]) != str(getattr(existing, field_name)):
                    raise ManifestIntegrityError(
                        f"baseline_index={raw['baseline_index']} is frozen — "
                        f"cannot change '{field_name}' on re-import"
                    )
            existing.content_available = raw.get("content_available", existing.content_available)
            existing.screening_status = raw.get("screening_status", existing.screening_status)
            existing.canonical_opportunity_id = raw.get(
                "canonical_opportunity_id", existing.canonical_opportunity_id
            )
            existing.duplicate_of = raw.get("duplicate_of", existing.duplicate_of)
            existing.source_evidence_reference = raw.get(
                "source_evidence_reference", existing.source_evidence_reference
            )
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)
            result.append(existing)
        else:
            entry = BaselineManifestEntry(**raw)
            session.add(entry)
            result.append(entry)

    session.commit()
    for entry in result:
        session.refresh(entry)
    return result


def next_incremental_index(session: Session) -> int:
    rows = session.exec(select(BaselineManifestEntry)).all()
    if not rows:
        return _FIRST_INCREMENTAL_INDEX
    return max(r.baseline_index for r in rows) + 1


def queue_incremental_post(session: Session, **kwargs) -> BaselineManifestEntry:
    """Add one post observed after the baseline freeze. Continues the
    index sequence — never reuses or reorders a baseline_index."""
    kwargs.setdefault("baseline_index", next_incremental_index(session))
    kwargs["incremental_after_baseline"] = True
    return freeze_baseline_manifest(session, [kwargs])[0]


def get_manifest_summary(session: Session) -> dict:
    rows = session.exec(select(BaselineManifestEntry)).all()
    baseline_rows = [r for r in rows if not r.incremental_after_baseline]
    incremental_rows = [r for r in rows if r.incremental_after_baseline]
    return {
        "baseline_entries_frozen": len(baseline_rows),
        "baseline_target": 149,
        "incremental_entries_queued": len(incremental_rows),
        "duplicates_merged": sum(1 for r in rows if r.duplicate_of is not None),
        "unscreened": sum(1 for r in rows if r.screening_status == "unscreened"),
    }
