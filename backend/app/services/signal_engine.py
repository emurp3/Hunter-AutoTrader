"""
Signal Engine — Public-Signal Copy Engine core service.

Ingest → Deduplicate → Score → Route (mirror/partial/watchlist/reject)

Public data sources only. Compliance-first.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from sqlmodel import Session, select

from app.models.copy_signal import CopySignal, SignalScanState
from app.services.sources.congress_feed import CongressFeedAdapter
from app.services.sources.sec_edgar import SecEdgarAdapter
from app.services.sources.crypto_signal import CryptoSignalAdapter
from app.services.sources.oge_278t import Oge278TAdapter
from app.config import ENABLE_VIP_AUTO_INVEST

logger = logging.getLogger(__name__)

HIGH_VALUE_COMMITTEES = {
    "armed services", "intelligence", "finance", "banking",
    "energy", "health", "commerce", "foreign relations",
}

# Executive roles that get a scoring boost (reused from 278T committee field)
HIGH_VALUE_EXEC_ROLES = {
    "president", "vice president", "secretary of treasury",
    "secretary of commerce", "secretary of state", "trade advisor",
    "doge", "special advisor",
}


def score_signal(signal: dict) -> float:
    score = 0.0
    src = str(signal.get("source", "")).lower()

    # Source base scores
    if "oge_278t" in src:
        score += 0.30   # Executive branch — highest alpha (decision-makers)
    elif "congress" in src:
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
    if any(r in committee for r in HIGH_VALUE_EXEC_ROLES):
        score += 0.10   # executive role bonus

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



# ── VIP Watchlist & Auto Micro-Invest ───────────────────────────────────────────────

# VIPs whose trades trigger automatic micro-invest regardless of normal threshold
VIP_WATCHLIST = {
    # ─── Executive Branch — OGE Form 278T (personal investment transactions) ──────
    "Trump, Donald J.": {"label": "President Trump",       "source": "oge_278t",  "ticker_override": None},
    "Trump, Donald":    {"label": "President Trump",       "source": "oge_278t",  "ticker_override": None},
    "Vance, JD":        {"label": "VP Vance",              "source": "oge_278t",  "ticker_override": None},
    "Vance, James D.": {"label": "VP Vance",              "source": "oge_278t",  "ticker_override": None},
    "Bessent, Scott":   {"label": "Sec. Bessent (Treasury)","source": "oge_278t", "ticker_override": None},
    "Lutnick, Howard":  {"label": "Sec. Lutnick (Commerce)","source": "oge_278t", "ticker_override": None},
    "Navarro, Peter":   {"label": "Peter Navarro (Trade)", "source": "oge_278t",  "ticker_override": None},
    "Musk, Elon":       {"label": "Elon Musk (DOGE)",      "source": "oge_278t",  "ticker_override": None},
    "Gabbard, Tulsi":  {"label": "DNI Gabbard",           "source": "oge_278t",  "ticker_override": None},
    "Kennedy, Robert F.": {"label": "Sec. RFK Jr. (HHS)", "source": "oge_278t",  "ticker_override": None},
    # ─── Presidential / Executive orbit via SEC Form 4 (DJT insiders) ──────────
    "TRUMP DONALD J":   {"label": "President Trump (DJT)", "source": "sec_form4", "ticker_override": "DJT"},
    "Trump Donald":     {"label": "President Trump (DJT)", "source": "sec_form4", "ticker_override": "DJT"},
    "NUNES DEVIN":      {"label": "Devin Nunes (DJT)",     "source": "sec_form4", "ticker_override": "DJT"},
    "Nunes Devin":      {"label": "Devin Nunes (DJT)",     "source": "sec_form4", "ticker_override": "DJT"},
    # ─── Congressional VIPs — STOCK Act Form 8 ──────────────────────────
    "Nancy Pelosi":     {"label": "Speaker Pelosi",        "source": "congress",  "ticker_override": None},
    "Pelosi Nancy":     {"label": "Speaker Pelosi",        "source": "congress",  "ticker_override": None},
    "Matt Gaetz":       {"label": "Rep. Gaetz",            "source": "congress",  "ticker_override": None},
    "Dan Crenshaw":     {"label": "Rep. Crenshaw",         "source": "congress",  "ticker_override": None},
    "Michael McCaul":   {"label": "Rep. McCaul",           "source": "congress",  "ticker_override": None},
    "Mitch McConnell":  {"label": "Sen. McConnell",        "source": "congress",  "ticker_override": None},
    "Chuck Schumer":    {"label": "Sen. Schumer",          "source": "congress",  "ticker_override": None},
    "Marco Rubio":      {"label": "Sen. Rubio",            "source": "congress",  "ticker_override": None},
    "Elizabeth Warren": {"label": "Sen. Warren",           "source": "congress",  "ticker_override": None},
    "Mark Warner":      {"label": "Sen. Warner",           "source": "congress",  "ticker_override": None},
    "Tommy Tuberville": {"label": "Sen. Tuberville",       "source": "congress",  "ticker_override": None},
    "Tommy Tubervill":  {"label": "Sen. Tuberville",       "source": "congress",  "ticker_override": None},
    "Josh Hawley":      {"label": "Sen. Hawley",           "source": "congress",  "ticker_override": None},
    "Pat Toomey":       {"label": "Sen. Toomey",           "source": "congress",  "ticker_override": None},
}

VIP_MICRO_INVEST_AMOUNT = 15.00   # legacy flat amount — superseded by confidence-scaled sizing below, kept only as _execute_vip_micro_invest's default
VIP_MAX_DAILY_SPEND     = 75.00   # max total per day across all VIP signals

# 2026-09-02 — confidence-scaled sizing, added after finding the flat
# $15/trade auto-execute had no relationship to signal confidence at all.
# amount = min(confidence * VIP_AUTO_INVEST_MAX_AMOUNT, VIP_AUTO_INVEST_MAX_AMOUNT)
# so a low-confidence match sizes small and a high-confidence match sizes up
# toward the cap. Anything that would size ABOVE the approval threshold does
# NOT auto-execute — it's logged as pending_approval (CopySignal.decision)
# plus a review_required alert, and stays that way until acted on manually.
# This means, deliberately, that ONLY small/low-confidence trades execute
# unattended right now; the trades big enough to matter require a human
# look first. Both numbers are meant to be revisited after watching real
# trigger frequency for a while — not a permanent policy.
VIP_AUTO_INVEST_MAX_AMOUNT = float(os.getenv("HUNTER_VIP_AUTO_INVEST_MAX_AMOUNT", "10.00"))
VIP_AUTO_INVEST_APPROVAL_THRESHOLD = float(os.getenv("HUNTER_VIP_AUTO_INVEST_APPROVAL_THRESHOLD", "5.00"))


def _match_vip(filer_name: str, source: str) -> dict | None:
    """Return VIP entry if filer matches watchlist, else None."""
    if not filer_name:
        return None
    name_lower = filer_name.lower()
    for key, vip in VIP_WATCHLIST.items():
        if key.lower() in name_lower:
            return {"key": key, **vip}
    return None


def _execute_vip_micro_invest(ticker: str, action: str, vip_label: str, amount: float) -> dict:
    """
    Place a real Alpaca notional micro-buy for a VIP signal at the given
    confidence-scaled amount (see VIP_AUTO_INVEST_MAX_AMOUNT /
    VIP_AUTO_INVEST_APPROVAL_THRESHOLD). Caller is responsible for only
    invoking this when amount <= VIP_AUTO_INVEST_APPROVAL_THRESHOLD.
    """
    import os
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not ticker or ticker.upper() in ("N/A", ""):
        return {"status": "skip", "reason": "no_ticker", "vip": vip_label}

    alpaca_enabled = os.getenv("ALPACA_ENABLED", "").lower() in ("1", "true", "yes")
    if not alpaca_enabled:
        _logger.info("VIP trigger [dry-run]: would buy $%.2f of %s for %s",
                     amount, ticker, vip_label)
        return {"status": "dry_run", "ticker": ticker,
                "amount": amount, "vip": vip_label}

    try:
        import httpx
        api_key    = (os.getenv("LIVE_API_KEY") or os.getenv("SANDBOX_API_KEY", "")).strip()
        secret_key = (os.getenv("LIVE_SECRET_KEY") or os.getenv("SANDBOX_SECRET_KEY", "")).strip()
        base_url   = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

        symbol = ticker.split(":")[0]
        side   = "buy" if (action or "buy").lower() != "sell" else "sell"

        resp = httpx.post(
            f"{base_url}/v2/orders",
            json={"symbol": symbol, "notional": str(amount),
                  "side": side, "type": "market", "time_in_force": "day"},
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=10,
        )

        if resp.status_code in (200, 201):
            order = resp.json()
            _logger.info("VIP MICRO-INVEST OK: $%.2f %s %s for %s | order=%s",
                         amount, side.upper(), symbol,
                         vip_label, order.get("id"))
            return {"status": "executed", "ticker": symbol, "amount": amount,
                    "action": side, "vip": vip_label, "alpaca_order_id": order.get("id")}
        else:
            _logger.warning("VIP micro-invest FAILED: %d %s | %s",
                            resp.status_code, resp.text[:200], vip_label)
            return {"status": "error", "code": resp.status_code, "vip": vip_label}

    except Exception as exc:
        _logger.exception("VIP micro-invest exception for %s: %s", vip_label, exc)
        return {"status": "exception", "error": str(exc), "vip": vip_label}


def _flag_vip_needs_approval(session: Session, vip: dict, raw: dict, amount: float, confidence: float) -> None:
    """
    Raise a review_required alert for a VIP signal whose confidence-scaled
    amount exceeds VIP_AUTO_INVEST_APPROVAL_THRESHOLD. Does not execute
    anything — the CopySignal record itself carries decision=pending_approval
    as the durable record; this alert is just for visibility.
    """
    try:
        from app.services import alerts as alert_svc
        from app.models.alert import AlertType, AlertPriority

        ticker = vip.get("ticker_override") or raw.get("ticker", "?")
        alert_svc.raise_alert(
            alert_type=AlertType.review_required,
            title=f"VIP signal needs approval: {vip['label']} — {ticker} (${amount:.2f})",
            body=(
                f"{vip['label']} matched a disclosed transaction on {ticker} "
                f"(confidence={confidence:.2f}). Confidence-scaled amount "
                f"${amount:.2f} exceeds the ${VIP_AUTO_INVEST_APPROVAL_THRESHOLD:.2f} "
                f"auto-execute threshold, so no trade was placed. Review via "
                f"/signals/vip-watchlist or /signals/feed and execute manually "
                f"if desired."
            ),
            session=session,
            priority=AlertPriority.high,
        )
    except Exception:
        logger.exception("Failed to raise VIP approval alert for %s", vip.get("label"))


def _record_vip_allocation(session: Session, vip: dict, raw: dict, exec_result: dict, confidence: float) -> None:
    """
    Previously, a successful VIP auto-invest hit Alpaca directly via
    httpx and nothing else -- no BudgetAllocation record, invisible in
    /budget/allocations, /budget/transactions, and the budget scoreboard,
    even though the trade was real and the money genuinely moved. Broker-
    reconciled capital totals were still accurate (broker truth wins,
    see get_broker_reconciled_capital_state), but there was no way to
    see WHICH trades were VIP-mirror trades, or how that lane is
    performing on its own. This records that allocation so it's visible
    and attributable, not just absorbed into the aggregate cash number.
    """
    try:
        from app.models.budget import BudgetAllocation, AllocationCategory, AllocationStatus
        from app.services.budget import get_open_budget

        budget = get_open_budget(session)
        if not budget:
            logger.warning("VIP allocation not recorded — no open budget cycle")
            return

        ticker = exec_result.get("ticker", raw.get("ticker", "?"))
        allocation = BudgetAllocation(
            weekly_budget_id=budget.id,
            allocation_name=f"VIP mirror: {vip['label']} — {ticker}",
            category=AllocationCategory.trading,
            amount_allocated=exec_result.get("amount", 0.0),
            rationale=(
                f"Confidence-scaled auto-invest (confidence={confidence:.2f}) on "
                f"{vip['label']}'s disclosed {raw.get('source', 'transaction')}. "
                f"Alpaca order {exec_result.get('alpaca_order_id', 'n/a')}."
            ),
            source_id=str(raw.get("source_id", "")) or None,
            approval_required=False,  # already executed under the standing $5-threshold auto-invest policy
            approved_by_commander=True,
            status=AllocationStatus.active,
        )
        session.add(allocation)
        session.commit()
    except Exception:
        logger.exception("Failed to record VIP allocation for %s", vip.get("label"))


def get_vip_watchlist() -> list[dict]:
    """Return the full VIP watchlist for the /signals/vip-watchlist endpoint."""
    return [
        {"name": k, "label": v["label"], "source": v["source"],
         "ticker_override": v.get("ticker_override")}
        for k, v in VIP_WATCHLIST.items()
    ]


def run_signal_scan(session: Session, days_back: int = 30) -> dict:
    adapters = [
        CongressFeedAdapter(),
        SecEdgarAdapter(),
        CryptoSignalAdapter(),
        Oge278TAdapter(),          # Executive branch 278T — Trump admin trades
    ]
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
            pre_decision = raw.pop("_pre_decision", None)
            confidence = score_signal(raw)
            _vip = _match_vip(raw.get("filer_name", ""), raw.get("source", ""))
            _vip_decision_override: tuple[str, str] | None = None
            if _vip:
                vip_amount = round(min(confidence * VIP_AUTO_INVEST_MAX_AMOUNT, VIP_AUTO_INVEST_MAX_AMOUNT), 2)
                if vip_amount > VIP_AUTO_INVEST_APPROVAL_THRESHOLD:
                    # High enough confidence/amount to matter — do NOT
                    # auto-execute. Log as pending_approval (durable, in the
                    # CopySignal record itself) and raise a review_required
                    # alert. Nothing places a trade until someone acts on it.
                    _flag_vip_needs_approval(session, _vip, raw, vip_amount, confidence)
                    _vip_decision_override = (
                        "pending_approval",
                        f"VIP match ${vip_amount:.2f} (conf={confidence:.2f}) exceeds "
                        f"${VIP_AUTO_INVEST_APPROVAL_THRESHOLD:.2f} auto-execute threshold — needs review",
                    )
                    errors.append(f"VIP:{_vip['label']}:pending_approval(${vip_amount:.2f})")
                elif ENABLE_VIP_AUTO_INVEST:
                    _vticker = _vip.get("ticker_override") or raw.get("ticker", "")
                    _vresult = _execute_vip_micro_invest(_vticker, raw.get("action", "buy"), _vip["label"], amount=vip_amount)
                    errors.append(f"VIP:{_vip['label']}:{_vresult['status']}(${vip_amount:.2f})")
                    if _vresult.get("status") == "executed":
                        _record_vip_allocation(session, _vip, raw, _vresult, confidence)
                else:
                    # Monitoring stays on; unattended real-money execution on
                    # a VIP name match requires HUNTER_ENABLE_VIP_AUTO_INVEST=true.
                    # The signal itself is still logged below via the normal
                    # decision/CopySignal path, just without placing a trade.
                    logger.info(
                        "VIP match logged (auto-invest disabled): %s ($%.2f, conf=%.2f) -- set "
                        "HUNTER_ENABLE_VIP_AUTO_INVEST=true to enable real execution",
                        _vip["label"], vip_amount, confidence,
                    )
            if _vip_decision_override:
                decision, reason = _vip_decision_override
            elif pre_decision and raw.get("asset_type") == "crypto":
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
