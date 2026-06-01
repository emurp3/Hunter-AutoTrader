import os
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.income_source import IncomeSource

router = APIRouter(prefix="/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    context_snapshot: dict


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, session: Session = Depends(get_session)):
    ctx = _gather_context(session)
    system_prompt = _build_system_prompt(ctx)

    try:
        import openai
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload.message},
            ],
            max_tokens=600,
        )
        response_text = completion.choices[0].message.content
    except Exception as exc:
        logger.error("OpenAI call failed: %s", exc)
        response_text = "Hunter AI is temporarily offline. Check your connection."

    return ChatResponse(
        response=response_text,
        context_snapshot={
            "account_cash": ctx.get("account_cash"),
            "top_opp_count": len(ctx.get("top_opps", [])),
            "signals_total": ctx.get("signals_total", 0),
        },
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
        from app.integration.brokerage.alpaca import AlpacaClient
        client = AlpacaClient()
        acct = client.get_account()
        ctx["account_cash"] = str(getattr(acct, "cash", None) or getattr(acct, "buying_power", "unknown"))
        ctx["buying_power"] = str(getattr(acct, "buying_power", "unknown"))
        ctx["account_status"] = str(getattr(acct, "status", "unknown"))
    except Exception:
        ctx["account_cash"] = "unknown"
        ctx["buying_power"] = "unknown"
        ctx["account_status"] = "unknown"

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

    return (
        "You are Hunter's onboard AI advisor. You have real-time access to the following Hunter state:\n\n"
        "ACCOUNT: Cash ${account_cash}, Buying Power ${buying_power}, Status: {account_status}\n"
        "CAPITAL STATE: Available ${available_capital}, Committed ${committed}\n\n"
        "TOP OPPORTUNITIES (ranked):\n{opps_text}\n\n"
        "TODAY'S ADVISOR OPP: {advisor_opp_title} via {advisor_opp_ticker} ({advisor_opp_lane})\n\n"
        "ACTIVE STRATEGIES: {strategy_names}\n"
        "SIGNALS: {signals_total} ingested\n"
        "FORGE OPPS: {forge_count} opportunities queued\n"
        "PERFORMANCE: {success_rate}% success rate, {completed} completed, {failed} failed\n\n"
        "Answer the user's question clearly and actionably. Be direct. If an action is required, "
        "specify the exact step. Reference specific opportunity names, tickers, and amounts from "
        "the data above when relevant. Keep responses under 250 words."
    ).format(opps_text=opps_text, **ctx)
