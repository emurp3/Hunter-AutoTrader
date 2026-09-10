"""
Shared LLM call helper for Hunter's generic research capability. Reuses
the exact advisor APIs (Venice/DeepSeek/Grok) already integrated and
configured for Hunter's daily-opportunity rotation (see
app.integration.advisor_bridge, app.services.daily_opportunity) — this is
Hunter's own existing infrastructure, not a new external dependency
Claude is bolting on.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import httpx

_ADVISOR_CONFIG = {
    "grok": {
        "url_env": "GROK_API_URL", "key_env": "GROK_API_KEY",
        "default_url": "https://api.x.ai/v1",
        "model_env": "GROK_MODEL", "default_model": "grok-3",
    },
    "venice": {
        "url_env": "VENICE_API_URL", "key_env": "VENICE_API_KEY",
        "default_url": "https://api.venice.ai/api/v1",
        "model_env": "VENICE_MODEL", "default_model": "llama-3.3-70b",
    },
    "deepseek": {
        "url_env": "DEEPSEEK_API_URL", "key_env": "DEEPSEEK_API_KEY",
        "default_url": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL", "default_model": "deepseek-chat",
    },
}
_FALLBACK_ORDER = ["grok", "venice", "deepseek"]


def any_advisor_configured() -> bool:
    return any(os.getenv(cfg["key_env"], "") for cfg in _ADVISOR_CONFIG.values())


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    client: httpx.Client,
    max_tokens: int = 700,
    temperature: float = 0.2,
    telemetry: Optional[dict] = None,
) -> Optional[dict]:
    """Call the first configured advisor and parse a JSON object from its
    response. Returns None if no advisor is configured or every attempt
    fails — callers must treat that as a genuine capability gap and never
    fabricate a result in its place.

    If `telemetry` is given, it's filled in place with which provider and
    model actually answered (or None/None if every attempt failed),
    whether a fallback past the primary (grok) occurred, and — when a
    fallback did occur — a short reason naming what happened to each
    earlier provider tried. Never includes API keys, request/response
    bodies, or prompt content — provider/model names and exception TYPE
    names only."""
    attempted: list[dict] = []
    for name in _FALLBACK_ORDER:
        cfg = _ADVISOR_CONFIG[name]
        api_key = os.getenv(cfg["key_env"], "")
        if not api_key:
            attempted.append({"provider": name, "outcome": "not_configured"})
            continue
        base_url = os.getenv(cfg["url_env"], cfg["default_url"])
        model = os.getenv(cfg["model_env"], cfg["default_model"])
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = client.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            parsed = json.loads(content)
            if telemetry is not None:
                telemetry["provider"] = name
                telemetry["model"] = model
                telemetry["fallback_occurred"] = bool(attempted)
                telemetry["fallback_reason"] = (
                    "; ".join(f"{a['provider']}: {a['outcome']}" for a in attempted) if attempted else None
                )
            return parsed
        except Exception as exc:  # noqa: BLE001
            attempted.append({"provider": name, "outcome": f"error:{type(exc).__name__}"})
            continue
    if telemetry is not None:
        telemetry["provider"] = None
        telemetry["model"] = None
        telemetry["fallback_occurred"] = None
        telemetry["fallback_reason"] = (
            "; ".join(f"{a['provider']}: {a['outcome']}" for a in attempted) if attempted else "no advisor configured"
        )
    return None
