"""
Advisor bridge — structured HTTP calls to Venice, DeepSeek, and Grok.

call_advisor(advisor_name, source, session) → AdvisorInput
  Sends opportunity context to the named advisor and persists the result.

Required env vars per advisor:
  VENICE_API_KEY, VENICE_API_URL     (defaults to https://api.venice.ai/api/v1)
  DEEPSEEK_API_KEY, DEEPSEEK_API_URL (defaults to https://api.deepseek.com/v1)
  GROK_API_KEY, GROK_API_URL         (defaults to https://api.x.ai/v1)

All three use OpenAI-compatible chat completion APIs.
"""

import json
import os
from typing import Optional

import httpx
from sqlmodel import Session

from app.models.advisor import AdvisorName, AdvisorRecommendation
from app.models.income_source import IncomeSource
from app.services.advisors import store_advisor_input

_ADVISOR_CONFIG = {
    AdvisorName.venice: {
        "url_env": "VENICE_API_URL",
        "key_env": "VENICE_API_KEY",
        "default_url": "https://api.venice.ai/api/v1",
        "model_env": "VENICE_MODEL",
        "default_model": "llama-3.3-70b",
    },
    AdvisorName.deepseek: {
        "url_env": "DEEPSEEK_API_URL",
        "key_env": "DEEPSEEK_API_KEY",
        "default_url": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
    },
    AdvisorName.grok: {
        "url_env": "GROK_API_URL",
        "key_env": "GROK_API_KEY",
        "default_url": "https://api.x.ai/v1",
        "model_env": "GROK_MODEL",
        "default_model": "grok-3",
    },
}

_SYSTEM_PROMPT = """You are an elite income opportunity analyst. Evaluate the opportunity and respond in valid JSON only.

Response format:
{
  "recommendation": "pursue" | "park" | "reject" | "escalate" | "monitor",
  "confidence": 0.0-1.0,
  "reasoning": "one paragraph reasoning"
}

Be decisive. Factor in profit potential, risk, and actionability."""


def _build_user_prompt(source: IncomeSource) -> str:
    return (
        f"Opportunity ID: {source.source_id}\n"
        f"Description: {source.description}\n"
        f"Estimated profit: ${source.estimated_profit:,.2f}\n"
        f"Category: {source.category or 'unspecified'}\n"
        f"Status: {source.status}\n"
        f"Origin: {source.origin_module or 'unspecified'}\n"
        f"Current confidence signal: {source.confidence or 'N/A'}\n"
        f"Notes: {source.notes or 'none'}\n"
        f"Next action: {source.next_action or 'none'}\n\n"
        "Evaluate this opportunity and provide your recommendation."
    )


def _parse_advisor_response(raw: str) -> tuple[str, float, str]:
    """Parse JSON response. Returns (recommendation, confidence, reasoning)."""
    try:
        data = json.loads(raw)
        rec = data.get("recommendation", AdvisorRecommendation.monitor)
        conf = float(data.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
        reasoning = data.get("reasoning", raw[:500])
        # Validate recommendation
        valid = {r.value for r in AdvisorRecommendation}
        if rec not in valid:
            rec = AdvisorRecommendation.monitor
        return rec, conf, reasoning
    except (json.JSONDecodeError, ValueError):
        return AdvisorRecommendation.monitor, 0.5, raw[:500]


def call_advisor(
    advisor_name: str,
    source: IncomeSource,
    session: Session,
    *,
    timeout: float = 30.0,
) -> Optional[object]:
    """
    Call the named advisor with opportunity context.
    Returns AdvisorInput on success, None if API key not configured.
    Raises httpx.HTTPError on network/API failure.
    """
    config = _ADVISOR_CONFIG.get(advisor_name)
    if not config:
        raise ValueError(f"Unknown advisor: {advisor_name}")

    api_key = os.getenv(config["key_env"], "")
    if not api_key:
        return None  # Advisor not configured — skip silently

    base_url = os.getenv(config["url_env"], config["default_url"])
    model = os.getenv(config["model_env"], config["default_model"])

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(source)},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    raw_content = data["choices"][0]["message"]["content"]
    recommendation, confidence, reasoning = _parse_advisor_response(raw_content)

    return store_advisor_input(
        source_id=source.source_id,
        advisor_name=advisor_name,
        recommendation=recommendation,
        reasoning=reasoning,
        session=session,
        confidence=confidence,
        raw_response_json=json.dumps(data),
    )


def call_all_advisors(source: IncomeSource, session: Session) -> dict:
    """
    Call all configured advisors in sequence.
    Returns summary dict: {advisor_name: result | error_message}
    """
    results = {}
    for name in (AdvisorName.venice, AdvisorName.deepseek, AdvisorName.grok):
        try:
            result = call_advisor(name, source, session)
            results[name] = result.recommendation if result else "not_configured"
        except Exception as exc:
            results[name] = f"error: {exc}"
    return results
