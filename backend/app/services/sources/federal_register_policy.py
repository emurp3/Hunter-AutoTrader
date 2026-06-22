"""
FederalRegisterAdapter — monitors the Federal Register for proposed rules,
final rules, and agency notices relevant to business opportunities.

Uses the free public Federal Register API — no auth required.
API docs: https://www.federalregister.gov/api/v1
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

API_BASE = "https://www.federalregister.gov/api/v1"
_TIMEOUT = 15
_UA = "HunterP2PEngine/1.0 PolicyScanner"

# Document types with highest business-opportunity signal
_PRIORITY_TYPES = {"RULE", "PRESDOCU", "NOTICE", "PRORULE"}

# Agencies with strongest revenue-opportunity signal
_HIGH_VALUE_AGENCIES = {
    "Department of Defense", "Department of Veterans Affairs",
    "Department of Health and Human Services", "Small Business Administration",
    "Department of Energy", "Department of Commerce",
    "General Services Administration", "Department of Transportation",
    "Department of Homeland Security", "Department of the Treasury",
}


class FederalRegisterAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "federal_register"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        params = {
            "fields[]": ["title", "abstract", "document_number", "type",
                          "agencies", "publication_date", "html_url", "action"],
            "order": "newest",
            "per_page": "20",
            "conditions[publication_date][gte]": since,
        }
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = client.get(f"{API_BASE}/documents.json", params=params)
                if resp.status_code != 200:
                    logger.warning("federal_register: HTTP %d", resp.status_code)
                    return []
                data = resp.json()
                results = data.get("results", [])
                logger.info("federal_register: fetched %d documents", len(results))
                return results
        except Exception as exc:
            logger.warning("federal_register: fetch failed — %s", exc)
            return []

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = (raw.get("title") or "").strip()
        url = (raw.get("html_url") or "").strip()
        if not title:
            return None

        doc_type = raw.get("type", "NOTICE")
        abstract = (raw.get("abstract") or "").strip()[:500]
        agencies = raw.get("agencies", []) or []
        agency_names = ", ".join(a.get("name", "") for a in agencies if a.get("name"))[:200]
        action = (raw.get("action") or "").strip()
        pub_date = raw.get("publication_date")

        # Boost confidence for high-value agencies and priority doc types
        confidence = 0.55
        if doc_type in _PRIORITY_TYPES:
            confidence += 0.10
        if any(name in agency_names for name in _HIGH_VALUE_AGENCIES):
            confidence += 0.15
        confidence = min(confidence, 0.90)

        source_id = hashlib.sha256(f"fed_reg|{raw.get('document_number', title)}".encode()).hexdigest()[:16]
        summary = abstract or action or title
        pub_dt: datetime | None = None
        if pub_date:
            try:
                pub_dt = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass

        return SourceOpportunity(
            source_id=source_id,
            title=f"{doc_type}: {title}"[:200],
            description=f"[Federal Register — {agency_names or 'Agency'}] {summary}",
            estimated_profit=0.0,
            currency="USD",
            confidence=confidence,
            next_action="Policy-to-Profit Engine: identify compliance, consulting, and implementation opportunities",
            origin_module="policy_engine",
            category="Regulatory",
            source_url=url or f"https://www.federalregister.gov",
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="federal_register",
            signal_type="regulatory_action",
            metadata={"doc_type": doc_type, "agencies": agency_names, "published": pub_date},
        )
