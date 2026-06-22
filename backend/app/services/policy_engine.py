"""
Policy-to-Profit Engine — Hunter's intelligence layer for political,
regulatory, legal, and economic events.

Flow:
  1. Run all P2P source adapters
  2. Deduplicate events against PolicyEvent table
  3. For each new event: call LLM (Grok -> Venice -> DeepSeek fallback)
  4. Parse structured JSON response into revenue opportunities
  5. Score each opportunity using Hunter's proprietary 0-100 scale
  6. Persist as IncomeSource records with origin_module="policy_engine"
  7. Push high-priority items as Alerts

The engine is callable as a scheduled task and via the /policy/scan endpoint.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from app.database.config import engine
from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.models.policy_event import PolicyEvent
from app.services.sources.base import SourceOpportunity
from app.services.sources.whitehouse_actions import WhitehouseActionsAdapter
from app.services.sources.whitehouse_briefing import WhitehouseBriefingAdapter
from app.services.sources.trump_tracker import TrumpTrackerAdapter
from app.services.sources.federal_register_policy import FederalRegisterAdapter
from app.services.sources.sam_gov_policy import SamGovAdapter
from app.services.sources.lawfare_tracker import LawfareAdapter
from app.services.sources.congress_legislation import CongressLegislationAdapter

logger = logging.getLogger(__name__)

# ── LLM Configuration (mirrors daily_opportunity.py patterns) ─────────────────
_GROK_URL = os.getenv("GROK_API_URL", "https://api.x.ai/v1")
_GROK_KEY = os.getenv("GROK_API_KEY", "")
_GROK_MODEL = os.getenv("GROK_MODEL", "grok-3")

_VENICE_URL = os.getenv("VENICE_API_URL", "https://api.venice.ai/api/v1")
_VENICE_KEY = os.getenv("VENICE_API_KEY", "")
_VENICE_MODEL = os.getenv("VENICE_MODEL", "llama-3.3-70b")

_DEEPSEEK_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
_DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
_DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_LLM_TIMEOUT = 45

# ── Opportunity Categories ─────────────────────────────────────────────────────
OPPORTUNITY_CATEGORIES = [
    "Government Contracting", "Healthcare", "Veterans", "Technology", "AI",
    "Manufacturing", "Education", "Construction", "Energy", "Agriculture",
    "Transportation", "Tax Policy", "Financial Markets", "Small Business",
    "Content Creation", "Consulting Services",
]

# ── P2P Analysis System Prompt ─────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are Hunter, an elite revenue intelligence agent for Commander EMurph.
Your mission: convert political/regulatory/legal events into actionable revenue opportunities.
Focus exclusively on opportunity identification — not political commentary.

Commander's profile:
- Senior Project Manager (Oracle Cerner, federal healthcare)
- Veteran background and veteran-network access
- Skills: IT project management, stakeholder management, consulting, federal contracting
- Revenue target: $5,000/month additional income
- Active interests: Government contracting, IT consulting, content creation, small business

Always respond with valid JSON only. No markdown, no preamble."""

_USER_PROMPT_TEMPLATE = """Analyze this political/regulatory event and extract maximum revenue opportunities:

SOURCE: {source_name}
TITLE: {title}
SUMMARY: {summary}

Return ONLY this JSON structure:
{{
  "what_happened": "2-3 sentence plain-language explanation",
  "why_it_matters": "business impact summary in 2-3 sentences",
  "affected_industries": ["list", "of", "industries"],
  "opportunity_categories": ["from: Government Contracting, Healthcare, Veterans, Technology, AI, Manufacturing, Education, Construction, Energy, Agriculture, Transportation, Tax Policy, Financial Markets, Small Business, Content Creation, Consulting Services"],
  "opportunities": [
    {{
      "title": "Specific opportunity title",
      "description": "Concrete description of the revenue opportunity",
      "opportunity_type": "Government Contract | Consulting | Content | Investment | Grant | Service",
      "revenue_potential_low": 500,
      "revenue_potential_high": 5000,
      "time_sensitivity_days": 30,
      "recommended_actions": ["action 1", "action 2", "action 3"],
      "score_factors": {{
        "revenue_potential": 7,
        "time_sensitivity": 8,
        "market_size": 6,
        "competition_low": 5,
        "capital_required_low": 8,
        "skill_match": 9,
        "ease_of_entry": 7,
        "confidence": 8
      }},
      "priority_level": "Critical | High | Medium | Low"
    }}
  ],
  "profile_impacts": {{
    "veteran": "How this affects veterans specifically",
    "government_contractor": "Contracting opportunities",
    "consultant": "Consulting demand created",
    "content_creator": "Content/media opportunity",
    "small_business": "Small business angle"
  }}
}}

Generate 2-5 specific, actionable opportunities. Be concrete — name the service, the agency, the gap being created."""


# ── LLM Call ──────────────────────────────────────────────────────────────────
def _call_llm(prompt: str) -> dict[str, Any] | None:
    """Call LLM with Grok -> Venice -> DeepSeek fallback."""
    providers = [
        {"name": "grok", "url": _GROK_URL, "key": _GROK_KEY, "model": _GROK_MODEL},
        {"name": "venice", "url": _VENICE_URL, "key": _VENICE_KEY, "model": _VENICE_MODEL},
        {"name": "deepseek", "url": _DEEPSEEK_URL, "key": _DEEPSEEK_KEY, "model": _DEEPSEEK_MODEL},
    ]

    for provider in providers:
        if not provider["key"]:
            continue
        try:
            with httpx.Client(timeout=_LLM_TIMEOUT) as client:
                resp = client.post(
                    f"{provider['url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {provider['key']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": provider["model"],
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("policy_engine: %s returned HTTP %d", provider["name"], resp.status_code)
                    continue
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()

                # Strip markdown code fences if present
                if content.startswith("```"):
                    content = content.split("```", 2)[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.rsplit("```", 1)[0].strip()

                parsed = json.loads(content)
                logger.info("policy_engine: LLM response from %s — %d opportunities", provider["name"], len(parsed.get("opportunities", [])))
                return parsed
        except json.JSONDecodeError as exc:
            logger.warning("policy_engine: %s returned invalid JSON — %s", provider["name"], exc)
        except Exception as exc:
            logger.warning("policy_engine: %s call failed — %s", provider["name"], exc)

    return None


# ── Opportunity Scoring ────────────────────────────────────────────────────────
def _compute_hunter_score(factors: dict[str, int]) -> float:
    """
    Compute proprietary Hunter Opportunity Score (0-100).
    Factors each 0-10: revenue_potential, time_sensitivity, market_size,
    competition_low (higher = less competition = better),
    capital_required_low (higher = less capital needed = better),
    skill_match, ease_of_entry, confidence.
    """
    weights = {
        "revenue_potential":   0.25,
        "time_sensitivity":    0.15,
        "market_size":         0.10,
        "competition_low":     0.10,
        "capital_required_low": 0.10,
        "skill_match":         0.15,
        "ease_of_entry":       0.10,
        "confidence":          0.05,
    }
    raw = sum(factors.get(k, 5) * w for k, w in weights.items())
    return round(min(raw * 10.0, 100.0), 1)


def _priority_band(score: float, priority_level: str) -> str:
    level_map = {
        "Critical": PriorityBand.critical,
        "High": PriorityBand.high,
        "Medium": PriorityBand.medium,
        "Low": PriorityBand.low,
    }
    if priority_level in level_map:
        return level_map[priority_level]
    if score >= 75:
        return PriorityBand.critical
    if score >= 55:
        return PriorityBand.high
    if score >= 35:
        return PriorityBand.medium
    return PriorityBand.low


# ── Event Processing ───────────────────────────────────────────────────────────
def _process_event(event: PolicyEvent, session: Session) -> int:
    """Run LLM analysis on a PolicyEvent and create IncomeSource records.
    Returns number of opportunities created."""
    prompt = _USER_PROMPT_TEMPLATE.format(
        source_name=event.source_name,
        title=event.title,
        summary=event.summary[:800],
    )

    analysis = _call_llm(prompt)
    if not analysis:
        event.processing_error = "LLM call failed — all providers exhausted"
        event.processed = True
        session.add(event)
        return 0

    # Store analysis on the event
    event.llm_analysis = json.dumps(analysis)
    event.affected_industries = json.dumps(analysis.get("affected_industries", []))
    event.opportunity_categories = json.dumps(analysis.get("opportunity_categories", []))
    event.processed = True

    opportunities_created = 0
    for opp in analysis.get("opportunities", []):
        try:
            score_factors = opp.get("score_factors", {})
            hunter_score = _compute_hunter_score(score_factors)
            priority = _priority_band(hunter_score, opp.get("priority_level", "Medium"))

            revenue_low = float(opp.get("revenue_potential_low", 0))
            revenue_high = float(opp.get("revenue_potential_high", 500))
            estimated_profit = (revenue_low + revenue_high) / 2

            actions = opp.get("recommended_actions", [])
            next_action = actions[0] if actions else "Review and assess opportunity"

            # Build enriched notes
            notes_parts = [
                f"Event: {event.title}",
                f"Source: {event.source_name}",
                f"What happened: {analysis.get('what_happened', '')}",
                f"Why it matters: {analysis.get('why_it_matters', '')}",
                f"Opp type: {opp.get('opportunity_type', '')}",
                f"Time sensitivity: {opp.get('time_sensitivity_days', 30)} days",
                f"Actions: {'; '.join(actions[:3])}",
                f"Profile impacts: {json.dumps(analysis.get('profile_impacts', {}))}",
                f"Policy event ID: {event.id}",
                f"Hunter Score: {hunter_score}/100",
            ]

            source = IncomeSource(
                source_id=f"p2p_{uuid.uuid4().hex[:12]}",
                title=opp.get("title", event.title)[:200],
                description=opp.get("description", "")[:1000],
                estimated_profit=round(estimated_profit, 2),
                currency="USD",
                status=SourceStatus.new,
                confidence=score_factors.get("confidence", 7) / 10.0,
                score=hunter_score,
                priority_band=priority,
                origin_module="policy_engine",
                category=opp.get("opportunity_type", "Policy Intelligence")[:100],
                next_action=next_action[:500],
                notes="\n".join(notes_parts)[:2000],
                date_found=date.today(),
                lane="policy",
            )
            session.add(source)
            opportunities_created += 1

        except Exception as exc:
            logger.warning("policy_engine: failed to create income source — %s", exc)

    event.opportunities_generated = opportunities_created
    session.add(event)
    return opportunities_created


# ── Source Registry ────────────────────────────────────────────────────────────
def _build_adapters() -> list:
    return [
        WhitehouseActionsAdapter(max_records=10),
        WhitehouseBriefingAdapter(max_records=10),
        TrumpTrackerAdapter(max_records=10),
        FederalRegisterAdapter(max_records=15),
        SamGovAdapter(max_records=15),
        LawfareAdapter(max_records=10),
        CongressLegislationAdapter(max_records=10),
    ]


# ── Main Scan ─────────────────────────────────────────────────────────────────
def run_policy_scan(session: Session | None = None) -> dict[str, Any]:
    """
    Full Policy-to-Profit scan:
      1. Run all source adapters
      2. Deduplicate against PolicyEvent table
      3. Process new events through LLM
      4. Create IncomeSource records
      5. Return summary report

    Can be called with an existing session or will create its own.
    """
    own_session = session is None
    if own_session:
        session = Session(engine)

    try:
        adapters = _build_adapters()
        total_fetched = 0
        total_new = 0
        total_opportunities = 0
        source_results: dict[str, dict] = {}

        for adapter in adapters:
            source = adapter.source_name()
            try:
                raw_opps: list[SourceOpportunity] = adapter.run()
                total_fetched += len(raw_opps)
                new_for_source = 0

                for opp in raw_opps:
                    content_hash = PolicyEvent.make_hash(source, opp.title, opp.source_url)

                    # Deduplication check
                    existing = session.exec(
                        select(PolicyEvent).where(PolicyEvent.content_hash == content_hash)
                    ).first()
                    if existing:
                        continue

                    # New event — persist it
                    event = PolicyEvent(
                        content_hash=content_hash,
                        source_name=source,
                        source_url=opp.source_url,
                        title=opp.title or "",
                        summary=opp.description or opp.title or "",
                        raw_text=None,
                        published_at=None,
                    )
                    session.add(event)
                    session.commit()
                    session.refresh(event)

                    # Process through LLM
                    n_opps = _process_event(event, session)
                    session.commit()
                    total_opportunities += n_opps
                    new_for_source += 1
                    total_new += 1

                source_results[source] = {
                    "fetched": len(raw_opps),
                    "new_events": new_for_source,
                    "health": adapter.health_status(),
                }
            except Exception as exc:
                logger.warning("policy_engine: adapter %s failed — %s", source, exc)
                source_results[source] = {"fetched": 0, "new_events": 0, "error": str(exc)}

        report = {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "total_sources": len(adapters),
            "total_events_fetched": total_fetched,
            "total_new_events": total_new,
            "total_opportunities_created": total_opportunities,
            "source_breakdown": source_results,
        }
        logger.info(
            "policy_engine: scan complete — fetched=%d new=%d opportunities=%d",
            total_fetched, total_new, total_opportunities,
        )
        return report

    finally:
        if own_session:
            session.close()


# ── Health Check ──────────────────────────────────────────────────────────────
def get_source_health() -> list[dict[str, Any]]:
    """Return health status of all P2P source adapters."""
    adapters = _build_adapters()
    return [adapter.health_status() for adapter in adapters]


# ── Dashboard Metrics ─────────────────────────────────────────────────────────
def get_dashboard_metrics(session: Session) -> dict[str, Any]:
    """Aggregate P2P dashboard metrics."""
    events = session.exec(select(PolicyEvent)).all()
    sources_from_policy = session.exec(
        select(IncomeSource).where(IncomeSource.origin_module == "policy_engine")
    ).all()

    total_events = len(events)
    processed_events = sum(1 for e in events if e.processed)
    total_opps = len(sources_from_policy)

    active_opps = [s for s in sources_from_policy if s.status not in (SourceStatus.rejected, SourceStatus.exhausted)]
    high_priority = [s for s in active_opps if s.priority_band in (PriorityBand.critical, PriorityBand.high)]

    estimated_monthly_value = sum(s.estimated_profit for s in active_opps)
    avg_score = (sum(s.score or 0 for s in active_opps) / len(active_opps)) if active_opps else 0

    # Recent events by source
    from collections import Counter
    source_counts = Counter(e.source_name for e in events)

    return {
        "total_events_tracked": total_events,
        "events_processed": processed_events,
        "events_pending": total_events - processed_events,
        "total_opportunities": total_opps,
        "active_opportunities": len(active_opps),
        "high_priority_opportunities": len(high_priority),
        "estimated_monthly_value": round(estimated_monthly_value, 2),
        "average_opportunity_score": round(avg_score, 1),
        "by_source": dict(source_counts),
        "top_opportunities": [
            {
                "id": s.id,
                "source_id": s.source_id,
                "title": s.title,
                "score": s.score,
                "priority_band": s.priority_band,
                "estimated_profit": s.estimated_profit,
                "category": s.category,
                "next_action": s.next_action,
            }
            for s in sorted(active_opps, key=lambda x: x.score or 0, reverse=True)[:10]
        ],
    }
