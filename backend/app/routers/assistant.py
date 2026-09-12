import os
import logging
from typing import Literal
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.auth.jwt import require_admin
from app.database.config import get_session
from app.models.income_source import IncomeSource

router = APIRouter(prefix="/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)


@router.get("/identity/status")
def identity_status(_user=Depends(require_admin)):
    """Which identity fields (DOB, SSN, address, etc.) are on file — names
    only, never values. The values themselves live only in this service's
    environment variables (see app/services/commander_identity.py) and are
    never returned by any endpoint in this app."""
    from app.services import commander_identity
    return commander_identity.get_identity_field_presence()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    context_snapshot: dict
    error_code: str | None = None
    provider: str = "deepseek"
    model: str | None = None
    usage: dict | None = None
    estimated_cost_usd: float | None = None


def _provider_error(exc: Exception) -> tuple[str, str]:
    status = getattr(exc, "status_code", None)
    body = str(exc).lower()
    if status in (401, 403):
        return "provider_authentication", "Hunter AI provider authentication failed."
    if "insufficient" in body and any(term in body for term in ("balance", "credit", "quota")):
        return "provider_balance", "Hunter AI provider balance is unavailable."
    if status == 429:
        return "provider_rate_limit", "Hunter AI provider is rate-limited. Please retry shortly."
    if "timeout" in body:
        return "provider_timeout", "Hunter AI provider timed out. Please retry."
    if status is not None and status >= 500:
        return "provider_unavailable", "Hunter AI provider is temporarily unavailable."
    return "inference_error", "Hunter AI could not complete that request."


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, session: Session = Depends(get_session)):
    ctx = _gather_context(session)
    system_prompt = _build_system_prompt(ctx)

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    error_code = None
    try:
        import openai
        client = openai.OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com"),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "30")),
        )
        history = [m.model_dump() for m in payload.history[-20:]]
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": payload.message},
            ],
            max_tokens=600,
            extra_body={"thinking": {"type": "disabled"}},
        )
        response_text = completion.choices[0].message.content
        usage = completion.usage.model_dump() if completion.usage else None
        logger.info("Hunter chat completed provider=deepseek model=%s usage=%s", model, usage)
    except Exception as exc:
        error_code, response_text = _provider_error(exc)
        logger.error("DeepSeek chat failed code=%s model=%s: %s", error_code, model, exc)
        usage = None

    return ChatResponse(
        response=response_text,
        context_snapshot={
            "account_cash": ctx.get("account_cash"),
            "top_opp_count": len(ctx.get("top_opps", [])),
            "signals_total": ctx.get("signals_total", 0),
        },
        error_code=error_code,
        model=model,
        usage=usage,
    )


def _gather_context(session: Session) -> dict:
    ctx: dict = {}

    try:
        opps = session.exec(
            select(IncomeSource).order_by(IncomeSource.score.desc()).limit(5)
        ).all()
        ctx["top_opps"] = [
            {
                "rank": i + 1,
                "title": getattr(o, "title", "Untitled"),
                "category": getattr(o, "category", "unknown"),
                "score": getattr(o, "score", 0),
                "estimated_profit": getattr(o, "estimated_profit", 0),
                "status": str(getattr(o, "status", "unknown")),
                "next_action": getattr(o, "score_rationale", "") or "Review opportunity details",
            }
            for i, o in enumerate(opps)
        ]
    except Exception as exc:
        logger.warning("Failed to fetch opportunities: %s", exc)
        ctx["top_opps"] = []

    try:
        from app.models.strategy import Strategy
        strats = session.exec(select(Strategy)).all()
        ctx["strategy_names"] = ", ".join(
            getattr(s, "name", "?") for s in strats[:5]
        ) if strats else "none"
    except Exception:
        ctx["strategy_names"] = "unavailable"

    try:
        from app.models.copy_signal import CopySignal
        ctx["signals_total"] = len(session.exec(select(CopySignal)).all())
    except Exception:
        ctx["signals_total"] = 0

    try:
        from app.models.forge import ForgeOpportunity
        ctx["forge_count"] = len(session.exec(select(ForgeOpportunity)).all())
    except Exception:
        ctx["forge_count"] = 0

    try:
        from app.models.execution_outcome import ExecutionOutcome
        outcomes = session.exec(select(ExecutionOutcome)).all()
        completed = sum(1 for o in outcomes if getattr(o, "outcome", "") == "success")
        failed = sum(1 for o in outcomes if getattr(o, "outcome", "") == "failure")
        total = len(outcomes)
        ctx["success_rate"] = round((completed / total) * 100) if total else 0
        ctx["completed"] = completed
        ctx["failed"] = failed
    except Exception:
        ctx["success_rate"] = 0
        ctx["completed"] = 0
        ctx["failed"] = 0

    try:
        from app.integration.brokerage.alpaca import get_alpaca_adapter
        acct = get_alpaca_adapter().get_balance()
        ctx["account_cash"] = str(acct.cash)
        ctx["buying_power"] = str(acct.buying_power)
        ctx["account_status"] = str(acct.status)
    except Exception as exc:
        logger.warning("Failed to fetch Alpaca account balance: %s", exc)
        ctx["account_cash"] = "unknown"
        ctx["buying_power"] = "unknown"
        ctx["account_status"] = "unknown"

    try:
        from app.models.hunter_ledger import CanonicalOpportunity, Disposition
        pending = session.exec(
            select(CanonicalOpportunity).where(
                CanonicalOpportunity.disposition == Disposition.pending_commander.value,
                CanonicalOpportunity.commander_response.is_(None),
            )
        ).all()
        ctx["commander_decisions"] = [
            {
                "id": o.canonical_opportunity_id,
                "mechanism": o.factual_mechanism,
                "checkpoint": o.required_commander_checkpoints or "no specific checkpoint recorded",
            }
            for o in pending
        ]
    except Exception as exc:
        logger.warning("Failed to fetch pending Commander decisions: %s", exc)
        ctx["commander_decisions"] = []

    try:
        from app.services import execution_accounting as acct
        ctx["quota"] = acct.get_quota_status(session)
    except Exception as exc:
        logger.warning("Failed to fetch quota status: %s", exc)
        ctx["quota"] = None

    try:
        from app.models.hunter_ledger import CanonicalOpportunity, ZERO_EXECUTION_DISPOSITIONS
        active = session.exec(
            select(CanonicalOpportunity)
            .where(CanonicalOpportunity.disposition.in_([d.value for d in ZERO_EXECUTION_DISPOSITIONS]))
            .order_by(CanonicalOpportunity.score.desc())
            .limit(10)
        ).all()
        ctx["ledger_queue"] = [
            {
                "id": o.canonical_opportunity_id,
                "lane": o.lane,
                "disposition": o.disposition,
                "mechanism": o.factual_mechanism,
                "next_action": o.next_action or "none recorded",
            }
            for o in active
        ]
    except Exception as exc:
        logger.warning("Failed to fetch ledger queue: %s", exc)
        ctx["ledger_queue"] = []

    try:
        from app.services import commander_documents as docs_svc
        ctx["capability_profile"] = docs_svc.get_capability_profile_text(session)
        ctx["document_count"] = len(docs_svc.list_documents(session))
    except Exception as exc:
        logger.warning("Failed to fetch commander documents: %s", exc)
        ctx["capability_profile"] = None
        ctx["document_count"] = 0

    try:
        from app.services import commander_identity
        ctx["identity_fields_present"] = commander_identity.get_identity_field_presence()
    except Exception as exc:
        logger.warning("Failed to fetch identity field presence: %s", exc)
        ctx["identity_fields_present"] = {}

    ctx["current_datetime_utc"] = datetime.now(timezone.utc).strftime("%A, %Y-%m-%d %H:%M UTC")

    ctx.setdefault("advisor_opp_title", "none")
    ctx.setdefault("advisor_opp_ticker", "n/a")
    ctx.setdefault("advisor_opp_lane", "n/a")
    ctx.setdefault("available_capital", "unknown")
    ctx.setdefault("committed", "unknown")

    return ctx


def _build_system_prompt(ctx: dict) -> str:
    opps_text = "\n".join(
        "{rank}. [{category}] {title} | Score: {score} | Est. Profit: ${estimated_profit} | Status: {status} | Next Action: {next_action}".format(**o)
        for o in ctx.get("top_opps", [])
    ) or "No ranked opportunities available."

    decisions = ctx.get("commander_decisions") or []
    if decisions:
        decisions_text = "\n".join(
            f"- [{d['id']}] {d['mechanism']}\n  Checkpoint: {d['checkpoint']}" for d in decisions
        )
    else:
        decisions_text = "None open right now."

    queue = ctx.get("ledger_queue") or []
    if queue:
        queue_text = "\n".join(
            f"- [{q['id']}] ({q['disposition']}) {q['mechanism']} | Next: {q['next_action']}" for q in queue
        )
    else:
        queue_text = "No active execution-ledger candidates right now."

    quota = ctx.get("quota")
    if quota:
        quota_text = (
            f"{quota['execution_count']}/{quota['execution_quota']} today (verdict: {quota['daily_verdict']}), "
            f"{quota['weekly_count']}/{quota['weekly_quota']} this week"
        )
    else:
        quota_text = "unavailable"

    identity_present = ctx.get("identity_fields_present") or {}
    on_file = [field for field, present in identity_present.items() if present]
    identity_text = ", ".join(on_file) if on_file else "none on file"

    prompt = (
        "You are Hunter AI, Hunter's Commander-facing conversational interface. You are not a "
        "generic assistant — you speak from Hunter's live operational state below, supplied fresh "
        "on every message. Do not describe yourself by a specific model name or training-data "
        "cutoff date; that information is not reliably known to you and stating it wrong actively "
        "misleads the Commander. If asked what you're based on, say you're Hunter's operational "
        "interface and that model detail is an implementation detail Commander can ask Claude "
        "about directly.\n\n"
        "When Commander asks for something you don't have a direct tool for, don't deflect to "
        "generic outside advice (e.g. \"try a job site\") before checking whether Hunter itself "
        "already has a path — Commander can upload reference documents (resumes, capability "
        "profiles) through the document-upload feature; {document_count} on file right now. "
        "Point to Hunter's own capability first, and only fall back to outside suggestions when "
        "nothing in Hunter's system actually covers the request.\n\n"
        "COMMANDER IDENTITY FIELDS ON FILE (names only — you are never shown the actual values, "
        "so never state, guess, or fabricate one; you can only confirm which fields exist): "
        "{identity_text}. These are stored outside this conversation entirely and are only ever "
        "read by a specific, separately Commander-approved step at the point an application "
        "actually needs them — never by you, never for display.\n\n"
        "CURRENT DATE/TIME: {current_datetime_utc}\n\n"
        "Your own training data has a cutoff and is NOT authoritative for anything current — "
        "today's date, current officeholders, current events, prices, or any other fact that "
        "changes over time. For questions like that, do not answer from memory: say plainly that "
        "it is outside your current operational context and, if it materially affects an "
        "opportunity, that Hunter's research engine (not this chat) is the correct path to look it "
        "up. Only state current-events facts you can see explicitly in the Hunter state below.\n\n"
        "ACCOUNT: Cash ${account_cash}, Buying Power ${buying_power}, Status: {account_status}\n"
        "CAPITAL STATE: Available ${available_capital}, Committed ${committed}\n\n"
        "TOP OPPORTUNITIES (ranked):\n{opps_text}\n\n"
        "TODAY'S ADVISOR OPP: {advisor_opp_title} via {advisor_opp_ticker} ({advisor_opp_lane})\n\n"
        "ACTIVE STRATEGIES: {strategy_names}\n"
        "SIGNALS: {signals_total} ingested\n"
        "FORGE OPPS: {forge_count} opportunities queued\n"
        "PERFORMANCE: {success_rate}% success rate, {completed} completed, {failed} failed\n\n"
        "EXECUTIONS (addendum ledger, receipt-gated — never count research, approvals, or "
        "deployments as an execution): {quota_text}\n\n"
        "ACTIVE EXECUTION-LEDGER QUEUE (candidates not yet executed, highest score first):\n"
        "{queue_text}\n\n"
        "OPEN COMMANDER DECISIONS (opportunities Hunter's research completed but that are "
        "blocked on YOUR input — credentials, identity, filings, signatures, and similar "
        "regulated/consequential actions are never yours to supply or approve on Commander's "
        "behalf):\n{decisions_text}\n\n"
        "If there are open Commander decisions, lead with them — ask for exactly what's needed, "
        "referencing the opportunity by name. Never assume an answer, never invent eligibility or "
        "identity details, and never claim an opportunity executed unless Hunter's own ledger shows "
        "a real receipt (see EXECUTIONS above). For everything else: answer the user's question "
        "clearly and actionably from the state above. Be direct. If an action is required, specify "
        "the exact step. Reference specific opportunity names, tickers, and amounts from the data "
        "above when relevant. Keep responses under 250 words."
    ).format(
        opps_text=opps_text, decisions_text=decisions_text, queue_text=queue_text,
        quota_text=quota_text, identity_text=identity_text, **ctx,
    )

    capability_profile = ctx.get("capability_profile")
    if capability_profile:
        # Appended after .format() rather than interpolated into the
        # template — this document is long, free-form Commander-authored
        # text and must not be parsed as a format string (a stray { or }
        # in it would otherwise raise).
        prompt += (
            "\n\nCOMMANDER'S CAPABILITY & EXPERIENCE PROFILE (authoritative — use this, and only "
            "this, to reason about opportunity fit; never invent a credential, license, or "
            "qualification beyond what it states):\n" + capability_profile
        )

    return prompt
