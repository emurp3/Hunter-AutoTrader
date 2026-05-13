"""
Signal Engine — Public-Signal Copy Engine core service.

Ingest → Deduplicate → Score → Route (mirror/partial/watchlist/reject)

Public data sources only. Compliance-first.
"""
from __future__ import annotations
import logging
from datetime import datetime
from sqlmodel import Session, select

from app.models.copy_signal import CopySignal, SignalScanState
from app.services.sources.congress_feed import CongressFeedAdapter
from app.services.sources.sec_edgar import SecEdgarAdapter

logger = logging.getLogger(__name__)

HIGH_VALUE_COMMITTEES = {
    "armed services", "intelligence", "finance", "banking",
    "energy", "health", "commerce", "foreign relations",
}


def score_signal(signal: dict) -> float:
    score = 0.0
    src = str(signal.get("source", "")).lower()
    if "congress" in src:
        score += 0.20
    elif "sec" in src:
        score += 0.12

    mid = signal.get("amount_midpoint") or 0
    if mid >= 250_000:
        score += 0.40
    elif mid >= 50_000:
        score += 0.25
    else:
        score += 0.10

    lat = signal.get("latency_hours") or 9999
    if lat <= 72:
        score += 0.25
    elif lat <= 720:
        score += 0.15
    else:
        score += 0.05

    committee = str(signal.get("committee") or "").lower()
    if any(c in committee for c in HIGH_VALUE_COMMITTEES):
        score += 0.10

    if str(signal.get("action", "")).lower() == "buy":
        score += 0.05
    if signal.get("ticker"):
        score += 0.05

    return round(min(score, 1.0), 3)


def route_signal(confidence: float, latency_hours, amount) -> tuple:
    lat = latency_hours or 9999
    amt = amount or 0
    if confidence >= 0.70 and lat <= 168 and amt >= 50_000:
        return "mirror", "High confidence, recent disclosure, significant amount"
    if confidence >= 0.45 and lat <= 720:
        return "partial_mirror", "Moderate confidence within 30-day window"
    if confidence >= 0.25:
        return "watchlist", "Low-moderate confidence; monitor for confirmation"
    return "reject", "Below actionable threshold"


def run_signal_scan(session: Session, days_back: int = 30) -> dict:
    adapters = [CongressFeedAdapter(), SecEdgarAdapter()]
    new_signals = 0
    skipped = 0
    errors = []

    for adapter in adapters:
        try:
            raw_signals = adapter.fetch_recent(days_back=days_back)
        except Exception as exc:
            errors.append(str(exc))
            continue

        for raw in raw_signals:
            existing = session.exec(
                select(CopySignal)
                .where(CopySignal.source == raw.get("source"))
                .where(CopySignal.source_id == str(raw.get("source_id", "")))
            ).first()
            if existing:
                skipped += 1
                continue
            # ticker may be empty for SEC Form 4 records resolved without a CIK match
            # allow through — scoring already applies a 0.05 bonus when ticker is present
            confidence = score_signal(raw)
            decision, reason = route_signal(
                confidence, raw.get("latency_hours"), raw.get("amount_midpoint"))

            signal = CopySignal(
                source=raw["source"],
                source_id=str(raw.get("source_id", "")),
                filer_name=raw.get("filer_name", "Unknown"),
                filer_type=raw.get("filer_type", "unknown"),
                committee=raw.get("committee"),
                ticker=raw.get("ticker", ""),
                asset_type=raw.get("asset_type", "stock"),
                action=raw.get("action", "buy"),
                amount_low=raw.get("amount_low"),
                amount_high=raw.get("amount_high"),
                amount_midpoint=raw.get("amount_midpoint"),
                trade_date=raw.get("trade_date"),
                disclosed_at=raw.get("disclosed_at"),
                latency_hours=raw.get("latency_hours"),
                confidence_score=confidence,
                decision=decision,
                decision_reason=reason,
                decision_at=datetime.utcnow(),
                risk_level="high" if confidence < 0.40 else ("medium" if confidence < 0.65 else "low"),
                raw_json=raw.get("raw_json"),
            )
            session.add(signal)
            new_signals += 1

        state = session.exec(
            select(SignalScanState).where(SignalScanState.source == adapter.source_name())
        ).first()
        if not state:
            state = SignalScanState(source=adapter.source_name())
        state.last_scan_at = datetime.utcnow()
        state.last_count = new_signals
        state.total_ingested = (state.total_ingested or 0) + new_signals
        session.add(state)

    session.commit()
    return {"new": new_signals, "skipped": skipped, "errors": errors}


def get_signal_summary(session: Session) -> dict:
    signals = session.exec(
        select(CopySignal).order_by(CopySignal.created_at.desc()).limit(200)
    ).all()
    by_decision: dict = {}
    by_source: dict = {}
    for s in signals:
        by_decision[s.decision] = by_decision.get(s.decision, 0) + 1
        by_source[s.source] = by_source.get(s.source, 0) + 1
    mirrors = [s for s in signals if s.decision == "mirror"]
    return {
        "total_ingested": len(signals),
        "by_decision": by_decision,
        "by_source": by_source,
        "mirror_count": len(mirrors),
        "top_mirrors": [
            {"ticker": s.ticker, "filer": s.filer_name, "confidence": s.confidence_score,
             "action": s.action, "disclosed_at": s.disclosed_at.isoformat() if s.disclosed_at else None}
            for s in mirrors[:10]
        ],
        "recent": [
            {"id": s.id, "ticker": s.ticker, "source": s.source, "filer": s.filer_name,
             "action": s.action, "decision": s.decision, "confidence": s.confidence_score,
             "amount": s.amount_midpoint, "latency_hours": s.latency_hours,
             "decision_reason": s.decision_reason,
             "disclosed_at": s.disclosed_at.isoformat() if s.disclosed_at else None,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in signals[:50]
        ],
    }
