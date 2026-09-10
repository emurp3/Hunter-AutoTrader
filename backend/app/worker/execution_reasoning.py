"""
Hunter's OBSERVE -> REASON -> ACT -> VERIFY execution-reasoning fallback.

Deterministic Playwright automation (fixed CSS selector lists) is the
fast path everywhere in app/worker/executors.py and stays first — it's
free, fast, and exactly right when a page matches expectations. But a
fixed selector list can only ever be as good as the guess that wrote it,
and a live site that doesn't match the guess used to mean escalating
back to Claude reading Render logs by hand, redeploying, and guessing
again. That is not a capability Hunter has on its own.

This module is that missing capability: when the deterministic path
can't find what it's looking for, Hunter observes the real page itself
(title, URL, visible text, and every real interactive element it can
see — never fabricated), asks its own existing operational reasoning
model (Hunter's advisor bridge — see
app/services/research/generic/llm_client.py, the same Grok/Venice/
DeepSeek route the research engine already uses live, NOT the separate
OpenAI-backed Hunter AI chat widget) one narrow question — what is the
next action that advances this objective — and acts on the answer using
only elements that are actually present on the page.

Hard boundaries, by construction, not by request:
  - The LLM is NEVER shown Commander's identity field VALUES. It cannot
    leak, guess, or "helpfully" insert them, because they never enter
    its context. It can only navigate (click/select/fill using values
    already visible on the page) or explicitly hand off to Hunter's
    existing hard-gated identity-fill path once it believes the real
    form has been reached.
  - The LLM never declares success. It only proposes navigation. Success
    is still established exclusively by the deterministic fill/submit/
    confirm path and app/services/execution_accounting.py's existing
    EXECUTED rules — this module cannot record an execution, a receipt,
    or a disposition change.
  - Repeated-state / no-progress detection bounds the loop — Hunter
    abandons a route rather than iterating forever on a page that isn't
    changing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

import httpx

from app.services.research.generic.llm_client import any_advisor_configured, call_llm_json

_INTERACTIVE_SCAN_JS = """
    () => Array.from(document.querySelectorAll(
        'a, button, input, select, textarea, [role="button"], [onclick]'
    ))
        .filter(el => el.offsetParent !== null)
        .slice(0, 40)
        .map((el, i) => {
            el.setAttribute('data-hunter-ref', String(i));
            const tag = el.tagName.toLowerCase();
            const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || '')
                .trim().slice(0, 80);
            const attrs = {};
            if (el.name) attrs.name = el.name;
            if (el.id) attrs.id = el.id;
            if (tag === 'a' && el.href) attrs.href = el.href;
            if (tag === 'input') attrs.type = el.type || 'text';
            if (tag === 'select') {
                attrs.options = Array.from(el.options || []).map(o => o.label || o.value).slice(0, 20);
            }
            return {ref: String(i), tag, text, attrs};
        })
"""

_REASON_SYSTEM_PROMPT = (
    "You are choosing the next browser action to advance a specific, real "
    "execution objective on a real web page. You will be shown the "
    "current page's title, URL, a short excerpt of its visible text, and "
    "a numbered list of real interactive elements actually present on "
    "the page (links, buttons, form fields, select options).\n\n"
    "Given the execution objective, current page, and available "
    "interactive elements, what is the next action that advances this "
    "opportunity toward its externally effective endpoint?\n\n"
    "Rules:\n"
    "- Choose an action ONLY among the elements actually listed. Never "
    "invent an element, selector, ref, or URL that isn't shown.\n"
    "- You may click a link or button, or select/fill using ONLY a value "
    "already visible on the page (e.g. one of a dropdown's own listed "
    "options, or a generic non-personal choice like 'Continue as Guest' "
    "or 'I agree'). You must NEVER supply, guess, or fabricate "
    "Commander's personal information (name, email, phone, date of "
    "birth, SSN, address) — you were not given that data and must not "
    "invent it.\n"
    "- If reaching the objective requires entering Commander's personal "
    "information into a field now visible on the page, respond with "
    "action \"handoff_to_identity_fill\" — a separate, already-vetted "
    "system fills that using Commander's real stored data.\n"
    "- If the page shows an anti-bot challenge, CAPTCHA, login wall, or "
    "otherwise requires access Hunter does not have, respond with action "
    "\"give_up\" and explain why in \"reasoning\" — never attempt to "
    "bypass it.\n"
    "- If no listed element makes further progress possible, respond "
    "with action \"give_up\".\n\n"
    "Respond in JSON only: {\"action\": "
    "\"click\"|\"select\"|\"fill\"|\"handoff_to_identity_fill\"|\"give_up\", "
    "\"ref\": \"<ref of the chosen element, or null>\", "
    "\"value\": \"<value for select/fill, drawn only from what's visible "
    "on the page, or null>\", \"reasoning\": \"<one sentence>\"}"
)

_MAX_ITERATIONS = 5


def _fingerprint(observation: dict[str, Any]) -> str:
    """A cheap signature of 'what the page currently looks like' for
    no-progress detection — same URL/title plus the same set of visible
    interactive elements counts as no movement, even if a click
    technically fired without changing anything."""
    stable = (
        observation.get("url", ""),
        observation.get("title", ""),
        tuple(
            (el.get("tag"), el.get("text"))
            for el in observation.get("interactive_elements", [])
        ),
    )
    return hashlib.sha256(repr(stable).encode("utf-8")).hexdigest()


def observe_page_state(page, *, objective: str, checkpoint: str = "") -> dict[str, Any]:
    """OBSERVE — real page state only, safe to hand to an LLM: title, URL,
    a short excerpt of visible text, and every visible interactive
    element with a short-lived numbered ref Hunter's own Playwright can
    resolve back to the real element. Never includes Commander's
    identity data."""
    frames = getattr(page, "frames", None) or [page]
    interactive: list[dict[str, Any]] = []
    for frame_idx, frame in enumerate(frames):
        try:
            els = frame.evaluate(_INTERACTIVE_SCAN_JS)
        except Exception:  # noqa: BLE001
            continue
        for el in els or []:
            el = dict(el)
            el["ref"] = f"f{frame_idx}_{el['ref']}"
            interactive.append(el)

    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = ""
    try:
        body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 1500) : ''")
    except Exception:  # noqa: BLE001
        body_text = ""

    return {
        "objective": objective,
        "checkpoint": checkpoint,
        "url": getattr(page, "url", ""),
        "title": title or "",
        "body_text": body_text or "",
        "interactive_elements": interactive,
    }


def reason_next_action(
    observation: dict[str, Any], *, client: httpx.Client, history: list[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """REASON — one narrow operational question to Hunter's own
    configured advisor (Grok primary, Venice then DeepSeek fallback — the
    same route the research engine already uses live). Returns
    (decision, telemetry); decision is None if no advisor is configured
    or every attempt fails, which callers must treat as a genuine
    capability gap, never fabricating a decision in its place. telemetry
    identifies which provider/model actually answered (see
    llm_client.call_llm_json) — this is observability only, never fed
    back into a future prompt or used to change control flow."""
    elements_summary = [
        {"ref": el["ref"], "tag": el["tag"], "text": el["text"], **el.get("attrs", {})}
        for el in observation["interactive_elements"]
    ]
    prior = [
        {"decision": h.get("decision"), "act_result": h.get("act_result")}
        for h in history
        if h.get("decision") is not None
    ][-3:]
    user_prompt = (
        f"Objective: {observation['objective']}\n"
        f"Checkpoint: {observation['checkpoint']}\n"
        f"Current URL: {observation['url']}\n"
        f"Page title: {observation['title']}\n"
        f"Visible text excerpt: {observation['body_text']}\n"
        f"Interactive elements: {json.dumps(elements_summary)}\n"
        f"Prior actions this attempt (avoid repeating a failed one): {json.dumps(prior)}"
    )
    telemetry: dict[str, Any] = {}
    decision = call_llm_json(_REASON_SYSTEM_PROMPT, user_prompt, client=client, max_tokens=400, telemetry=telemetry)
    return decision, telemetry


_IDENTITY_KEYWORDS = ("ssn", "social", "dob", "birth", "address", "email", "phone")


def _looks_like_identity_value(value: Optional[str], identity_fields: dict[str, str]) -> bool:
    """Defense in depth — the LLM is never given identity_fields values,
    so it structurally cannot know them, but this guards against any
    coincidental match anyway before a fill/select ever reaches a real
    field, since Commander's rule on identity data is absolute."""
    if not value:
        return False
    lowered = value.strip().lower()
    for known_value in identity_fields.values():
        if known_value and known_value.strip().lower() == lowered:
            return True
    return False


def _resolve_ref(page, ref: str):
    frame_part, _, el_idx = ref.partition("_")
    frame_idx = int(frame_part.lstrip("f"))
    frames = getattr(page, "frames", None) or [page]
    frame = frames[frame_idx]
    return frame.locator(f'[data-hunter-ref="{el_idx}"]')


def act_on_decision(page, decision: dict[str, Any], identity_fields: dict[str, str]) -> dict[str, Any]:
    """ACT — convert the model's decision into a real, constrained browser
    operation against an element Hunter's own OBSERVE step actually
    found on the page. Never accepts a fill/select value that matches
    one of Commander's real identity field values."""
    action = decision.get("action")
    if action == "give_up":
        return {"progressed": False, "give_up": True, "reasoning": decision.get("reasoning")}
    if action == "handoff_to_identity_fill":
        return {"progressed": False, "handoff_to_identity_fill": True}

    ref = decision.get("ref")
    if not ref:
        return {"progressed": False, "error": "no ref given for a click/select/fill action"}
    try:
        locator = _resolve_ref(page, ref)
    except Exception as exc:  # noqa: BLE001
        return {"progressed": False, "error": f"could not resolve ref {ref}: {exc}"}
    if locator.count() == 0:
        return {"progressed": False, "error": f"ref {ref} not found on the page"}

    new_page = None
    try:
        if action == "click":
            # A link to a genuinely different destination (e.g. a
            # marketing site handing off to a separate intake platform on
            # another domain) very commonly opens in a new tab rather
            # than navigating the current one — check for that before
            # assuming the click was a same-page navigation.
            context_pages_before = None
            try:
                context_pages_before = list(page.context.pages)
            except Exception:  # noqa: BLE001
                context_pages_before = None

            locator.first.click(timeout=10000)

            if context_pages_before is not None:
                page.wait_for_timeout(1500)
                try:
                    current_pages = list(page.context.pages)
                except Exception:  # noqa: BLE001
                    current_pages = context_pages_before
                if len(current_pages) > len(context_pages_before):
                    new_page = current_pages[-1]

            # A click may trigger real navigation on a slow real-world
            # site — give it a real chance to land before VERIFY samples
            # the page again, rather than a flat short wait that can
            # mistake "still loading" for "no progress" and abandon a
            # route that was actually working.
            target_page = new_page or page
            try:
                target_page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
        elif action in ("select", "fill"):
            value = decision.get("value")
            if _looks_like_identity_value(value, identity_fields):
                return {"progressed": False, "error": "refused: proposed value matches Commander identity data"}
            if not value:
                return {"progressed": False, "error": f"no value given for '{action}' action"}
            if action == "select":
                locator.first.select_option(label=value)
            else:
                locator.first.fill(value)
        else:
            return {"progressed": False, "error": f"unknown action '{action}'"}
    except Exception as exc:  # noqa: BLE001
        return {"progressed": False, "error": f"action failed: {exc}"}

    page.wait_for_timeout(1200)
    result: dict[str, Any] = {"progressed": True}
    if new_page is not None:
        result["new_page"] = new_page
    return result


def run_observe_reason_act_verify(
    page,
    *,
    objective: str,
    identity_fields: dict[str, str],
    client: httpx.Client,
    max_iterations: int = _MAX_ITERATIONS,
) -> dict[str, Any]:
    """The full OBSERVE -> REASON -> ACT -> VERIFY loop. Returns
    {"success": bool, "trace": [...]}. success=True means either the
    loop reached a point where Hunter's existing deterministic
    identity-fill path should be retried (handoff_to_identity_fill), or
    the LLM believes it has fully solved a non-identity navigation goal.
    This function NEVER marks an execution successful on Commander's
    behalf — it only ever returns whether further deterministic action
    is warranted; the caller's own deterministic fill/submit/confirm
    path remains the sole source of truth. The returned "page" is the
    page the loop ended on — a click that opened a new tab (e.g. a
    marketing site handing off to a separate intake platform) switches
    the working page for the rest of the loop, and the caller must
    continue operating on whatever page comes back here, not its
    original reference."""
    trace: list[dict[str, Any]] = []
    if not any_advisor_configured():
        trace.append({"outcome": "no_advisor_configured"})
        return {"success": False, "trace": trace, "page": page}

    current_page = page
    seen_fingerprints: set[str] = set()
    checkpoint = f"reach a point where these fields can be filled: {sorted(identity_fields.keys())}"

    for i in range(max_iterations):
        observation = observe_page_state(current_page, objective=objective, checkpoint=checkpoint)
        fp = _fingerprint(observation)
        if fp in seen_fingerprints:
            trace.append({"iteration": i, "outcome": "no_progress_repeated_state"})
            return {"success": False, "trace": trace, "page": current_page}
        seen_fingerprints.add(fp)

        decision, reasoning_telemetry = reason_next_action(observation, client=client, history=trace)
        if not decision:
            trace.append({
                "iteration": i,
                "outcome": "no_advisor_response",
                "reasoning_provider": reasoning_telemetry.get("provider"),
                "reasoning_model": reasoning_telemetry.get("model"),
                "fallback_occurred": reasoning_telemetry.get("fallback_occurred"),
                "fallback_reason": reasoning_telemetry.get("fallback_reason"),
            })
            return {"success": False, "trace": trace, "page": current_page}

        step: dict[str, Any] = {
            "iteration": i,
            "url": observation["url"],
            "decision": decision,
            "reasoning_provider": reasoning_telemetry.get("provider"),
            "reasoning_model": reasoning_telemetry.get("model"),
            "fallback_occurred": reasoning_telemetry.get("fallback_occurred"),
            "fallback_reason": reasoning_telemetry.get("fallback_reason"),
        }

        if decision.get("action") == "give_up":
            step["outcome"] = "llm_gave_up"
            trace.append(step)
            return {"success": False, "trace": trace, "page": current_page}

        if decision.get("action") == "handoff_to_identity_fill":
            step["outcome"] = "handoff_to_identity_fill"
            trace.append(step)
            return {"success": True, "trace": trace, "page": current_page}

        act_result = act_on_decision(current_page, decision, identity_fields)
        new_page = act_result.pop("new_page", None)
        step["act_result"] = act_result
        trace.append(step)
        if new_page is not None:
            current_page = new_page
            # A new tab is unrelated DOM state to whatever the old tab
            # showed — don't let an old fingerprint falsely flag it as
            # "already seen".
            seen_fingerprints.clear()

    trace.append({"outcome": "max_iterations_reached"})
    return {"success": False, "trace": trace, "page": current_page}
