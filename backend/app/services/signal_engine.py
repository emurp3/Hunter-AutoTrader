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
from app.services.sources.crypto_signal import CryptoSignalAdapter

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



# ── VIP Watchlist & Auto Micro-Invest ─────────────────────────────────────────

# VIPs whose trades trigger automatic micro-invest regardless of normal threshold
VIP_WATCHLIST = {
    # Presidential / Executive orbit (via SEC Form 4 - DJT insiders)
    "TRUMP DONALD J":  {"label": "President Trump",      "source": "sec_form4", "ticker_override": "DJT"},
    "Trump Donald":    {"label": "President Trump",      "source": "sec_form4", "ticker_override": "DJT"},
    "NUNES DEVIN":     {"label": "Devin Nunes (DJT)",    "source": "sec_form4", "ticker_override": "DJT"},
    "Nunes Devin":     {"label": "Devin Nunes (DJT)",    "source": "sec_form4", "ticker_override": "DJT"},
    # Congressional VIPs - House
    "Nancy Pelosi":    {"label": "Speaker Pelosi",       "source": "congress",  "ticker_override": None},
    "Pelosi Nancy":    {"label": "Speaker Pelosi",       "source": "congress",  "ticker_override": None},
    "Matt Gaetz":      {"label": "Rep. Gaetz",           "source": "congress",  "ticker_override": None},
    "Dan Crenshaw":    {"label": "Rep. Crenshaw",        "source": "congress",  "ticker_override": None},
    "Michael McCaul":  {"label": "Rep. McCaul",          "source": "congress",  "ticker_override": None},
    # Congressional VIPs - Senate
    "Mitch McConnell": {"label": "Sen. McConnell",       "source": "congress",  "ticker_override": None},
    "Chuck Schumer":   {"label": "Sen. Schumer",         "source": "congress",  "ticker_override": None},
    "Marco Rubio":     {"label": "Sen. Rubio",           "source": "congress",  "ticker_override": None},
    "Elizabeth Warren":{"label": "Sen. Warren",          "source": "congress",  "ticker_override": None},
    "Mark Warner":     {"label": "Sen. Warner",          "source": "congress",  "ticker_override": None},
    "Tommy Tuberville":{"label": "Sen. Tuberville",      "source": "congress",  "ticker_override": None},
    "Tommy Tubervill": {"label": "Sen. Tuberville",      "source": "congress",  "ticker_override": None},
    "Josh Hawley":     {"label": "Sen. Hawley",          "source": "congress",  "ticker_override": None},
    "Pat Toomey":      {"label": "Sen. Toomey",          "source": "congress",  "ticker_override": None},
}

VIP_MICRO_INVEST_AMOUNT = 15.00   # dollars per VIP signal (notional)
VIP_MAX_DAILY_SPEND     = 75.00   # max total per day across all VIP signals


def _match_vip(filer_name: str, source: str) -> dict | None:
    """Return VIP entry if filer matches watchlist, else None."""
    if not filer_name:
        return None
    name_lower = filer_name.lower()
    for key, vip in VIP_WATCHLIST.items():
        if key.lower() in name_lower:
            return {"key": key, **vip}
    return None


def _execute_vip_micro_invest(ticker: str, action: str, vip_label: str) -> dict:
    """
    Place a real Alpaca notional micro-buy for a VIP signal.
    Capped at VIP_MICRO_INVEST_AMOUNT per trade.
    """
    import os
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not ticker or ticker.upper() in ("N/A", ""):
        return {"status": "skip", "reason": "no_ticker", "vip": vip_label}

    alpaca_enabled = os.getenv("ALPACA_ENABLED", "").lower() in ("1", "true", "yes")
    if not alpaca_enabled:
        _logger.info("VIP trigger [dry-run]: would buy $%.2f of %s for %s",
                     VIP_MICRO_INVEST_AMOUNT, ticker, vip_label)
        return {"status": "dry_run", "ticker": ticker,
                "amount": VIP_MICRO_INVEST_AMOUNT, "vip": vip_label}

    try:
        import httpx
        api_key    = (os.getenv("LIVE_API_KEY") or os.getenv("SANDBOX_API_KEY", "")).strip()
        secret_key = (os.getenv("LIVE_SECRET_KEY") or os.getenv("SANDBOX_SECRET_KEY", "")).strip()
        base_url   = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

        symbol = ticker.split(":")[0]  # strip :US exchange suffix
        side   = "buy" if (action or "buy").lower() != "sell" else "sell"

        resp = httpx.post(
            f"{base_url}/v2/orders",
            json={"symbol": symbol, "notional": str(VIP_MICRO_INVEST_AMOUNT),
                  "side": side, "type": "market", "time_in_force": "day"},
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=10,
        )

        if resp.status_code in (200, 201):
            order = resp.json()
            _logger.info("VIP MICRO-INVEST OK: $%.2f %s %s for %s | order=%s",
                         VIP_MICRO_INVEST_AMOUNT, side.upper(), symbol,
                         vip_label, order.get("id"))
            return {"status": "executed", "ticker": symbol, "amount": VIP_MICRO_INVEST_AMOUNT,
                    "action": side, "vip": vip_label, "alpaca_order_id": order.get("id")}
        else:
            _logger.warning("VIP micro-invest FAILED: %d %s | %s",
                            resp.status_code, resp.text[:200], vip_label)
            return {"status": "error", "code": resp.status_code, "vip": vip_label}

    except Exception as exc:
        _logger.exception("VIP micro-invest exception for %s: %s", vip_label, exc)
        return {"status": "exception", "error": str(exc), "vip": vip_label}


def get_vip_watchlist() -> list[dict]:
    """Return the full VIP watchlist for the /signals/vip-watchlist endpoint."""
    return [
        {"name": k, "label": v["label"], "source": v["source"],
         "ticker_override": v.get("ticker_override")}
        for k, v in VIP_WATCHLIST.items()
    ]


def run_signal_scan(session: Session, days_back: int = 30) -> dict:
    adapters = [CongressFeedAdapter(), SecEdgarAdapter(), CryptoSignalAdapter()]
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
            # Use pre-computed decision from crypto adapter if present
            pre_decision = raw.pop("_pre_decision", None)
            confidence = score_signal(raw)
            # VIP check: auto micro-invest if watchlist match
            _vip = _match_vip(raw.get("filer_name", ""), raw.get("source", ""))
            if _vip:
                _vticker = _vip.get("ticker_override") or raw.get("ticker", "")
                _vresult = _execute_vip_micro_invest(_vticker, raw.get("action", "buy"), _vip["label"])
                errors.append(f"VIP:{_vip['label']}:{_vresult['status']}")
            if pre_decision and raw.get("asset_type") == "crypto":
                decision, reason = pre_decision, f"CoinGecko velocity signal: {pre_decision}"
            else:
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
