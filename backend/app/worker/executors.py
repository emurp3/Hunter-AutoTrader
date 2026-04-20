from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class WorkerExecutionError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        escalation_type: str = "unrecoverable_failure",
        error_text: str | None = None,
        page_url: str | None = None,
        screenshot_path: str | None = None,
        trace_reference: str | None = None,
        engine: str = "playwright",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.escalation_type = escalation_type
        self.error_text = error_text or reason
        self.page_url = page_url
        self.screenshot_path = screenshot_path
        self.trace_reference = trace_reference
        self.engine = engine


class RetryableExecutionError(WorkerExecutionError):
    pass


@dataclass
class WorkerResult:
    outcome: dict[str, Any]
    notes: str = ""
    engine: str = "playwright"
    page_url: str | None = None
    screenshot_path: str | None = None
    trace_reference: str | None = None


def _artifact_dir(task_id: str) -> Path:
    root = Path(os.getenv("HUNTER_WORKER_ARTIFACTS_DIR", "/tmp/hunter-worker-artifacts"))
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_text_artifact(task_id: str, filename: str, content: str) -> str:
    path = _artifact_dir(task_id) / filename
    path.write_text(content, encoding="utf-8")
    return str(path)


def execute_task(task: dict[str, Any], worker_id: str) -> WorkerResult:
    task_type = task.get("task_type") or "generic_execution"
    spec = _decode_json(task.get("spec_payload"))
    if task_type == "generic_execution" and _is_trading_generic_spec(spec):
        return WorkerResult(
            outcome={
                "skipped": True,
                "skip_reason": "trading_sources_execute_via_broker_pipeline",
                "source_id": spec.get("source_id"),
            },
            notes="Skipped obsolete generic trading task. Hunter now routes trading sources through broker execution.",
            engine="direct_api",
        )
    if task_type == "digital_product_launch":
        return _execute_digital_product(task, spec)
    if task_type == "service_outreach":
        return _execute_service_outreach(task, spec)
    if task_type == "marketplace_listing":
        return _execute_marketplace_listing(task, spec, worker_id)
    raise WorkerExecutionError(
        f"Unsupported task_type: {task_type}",
        escalation_type="unrecoverable_failure",
        error_text=f"No hosted handler implemented for {task_type}",
    )


def _is_trading_generic_spec(spec: dict[str, Any]) -> bool:
    category = str(spec.get("category") or "").strip().lower()
    origin = str(spec.get("origin_module") or "").strip().lower()
    notes = str(spec.get("notes") or "").lower()
    description = str(spec.get("description") or "").lower()
    return (
        category == "trading"
        or origin == "autotrader"
        or "symbol:" in notes
        or "symbol:" in description
    )


def _decode_json(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not payload:
        return {}
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}
    return {}


def _execute_digital_product(task: dict[str, Any], spec: dict[str, Any]) -> WorkerResult:
    listing = spec.get("listing") or {}
    product_name = spec.get("description") or listing.get("title") or "Digital product"
    target_buyer = _extract_note_field(spec.get("notes"), "target_buyer") or "small business owner"
    draft = _claude_text(
        "Write a concise digital product launch brief with sections for summary, buyer, promise, deliverables, and first publish steps.\n"
        f"Product: {product_name}\nTarget buyer: {target_buyer}\n"
    )
    artifact_path = _write_text_artifact(task["task_id"], "digital_product_brief.md", draft)
    outcome = {
        "product_spec_generated": True,
        "product_name": product_name,
        "target_buyer": target_buyer,
        "artifact_path": artifact_path,
        "publish_ready": True,
    }
    return WorkerResult(
        outcome=outcome,
        notes="Hosted HVA generated a digital product brief.",
        engine="claude_cu",
        trace_reference=artifact_path,
    )


def _execute_service_outreach(task: dict[str, Any], spec: dict[str, Any]) -> WorkerResult:
    details = spec.get("service_outreach") or {}
    contact_email = details.get("contact_email")
    contact_url = details.get("contact_url")
    if not contact_email and not contact_url:
        raise WorkerExecutionError(
            "No contact route available for service outreach",
            escalation_type="unrecoverable_failure",
            error_text="Missing contact_email and contact_url in task spec",
            engine="claude_cu",
        )
    business_type = details.get("business_type") or spec.get("category") or "business"
    search_query = details.get("search_query") or spec.get("description") or business_type
    draft = _claude_text(
        "Draft a short, respectful cold outreach email for a local business prospect.\n"
        f"Business type: {business_type}\nSearch query: {search_query}\n"
    )
    artifact_path = _write_text_artifact(task["task_id"], "service_outreach_draft.md", draft)
    outcome = {
        "draft_created": True,
        "contact_email": contact_email,
        "contact_url": contact_url,
        "artifact_path": artifact_path,
        "search_query": search_query,
    }
    return WorkerResult(
        outcome=outcome,
        notes="Hosted HVA prepared service outreach copy.",
        engine="claude_cu",
        trace_reference=artifact_path,
    )


def _execute_marketplace_listing(task: dict[str, Any], spec: dict[str, Any], worker_id: str) -> WorkerResult:
    source_id = task.get("source_id")
    task_id = task.get("task_id")
    login_email = os.getenv("MARKETPLACE_FB_EMAIL", "").strip()
    login_password = os.getenv("MARKETPLACE_FB_PASSWORD", "").strip()
    if not login_email or not login_password:
        raise WorkerExecutionError(
            "Facebook Marketplace credentials missing",
            escalation_type="credentials_required",
            error_text="MARKETPLACE_FB_EMAIL and/or MARKETPLACE_FB_PASSWORD not set",
        )

    screenshot_path: str | None = None
    page_url: str | None = None
    trace_reference: str | None = None
    listing = spec.get("listing") or {}
    title = listing.get("title") or spec.get("description") or "Hunter Marketplace Listing"
    description = listing.get("description") or spec.get("notes") or title
    price = listing.get("price") or listing.get("listing_price") or 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=os.getenv("HUNTER_PLAYWRIGHT_HEADLESS", "true").lower() != "false",
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()
        trace_path = _artifact_dir(task_id) / "marketplace-trace.zip"
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=60000)
            page_url = page.url
            _fill_if_visible(page, ['input[name="email"]', 'input#email'], login_email)
            _fill_if_visible(page, ['input[name="pass"]', 'input#pass'], login_password)
            _click_if_visible(page, ['button[name="login"]', 'button[type="submit"]'])
            page.wait_for_timeout(4000)
            page_url = page.url

            if _is_checkpoint(page):
                screenshot_path = str(_artifact_dir(task_id) / "facebook-checkpoint.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Facebook checkpoint or challenge detected",
                    escalation_type="platform_lockout",
                    error_text="Facebook checkpoint/challenge detected during hosted worker login",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            if _login_form_still_visible(page):
                raise RetryableExecutionError(
                    "Facebook login did not complete",
                    escalation_type="unrecoverable_failure",
                    error_text="Login form still visible after submit",
                    page_url=page.url,
                )

            page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded", timeout=60000)
            page_url = page.url
            page.wait_for_timeout(5000)

            if _is_checkpoint(page):
                screenshot_path = str(_artifact_dir(task_id) / "marketplace-checkpoint.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Facebook checkpoint or challenge detected",
                    escalation_type="platform_lockout",
                    error_text="Checkpoint/challenge detected on Marketplace create page",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            screenshot_path = str(_artifact_dir(task_id) / "marketplace-create-page.png")
            page.screenshot(path=screenshot_path, full_page=True)
            outcome = {
                "facebook_login_success": True,
                "marketplace_page_loaded": True,
                "listing_title": title,
                "listing_price": price,
                "listing_description_excerpt": description[:280],
                "publish_step_completed": False,
                "worker_mode": "hosted_hva_prepare_only",
            }
            return WorkerResult(
                outcome=outcome,
                notes="Hosted HVA logged into Facebook Marketplace and opened the item create page.",
                engine="playwright",
                page_url=page_url,
                screenshot_path=screenshot_path,
                trace_reference=str(trace_path),
            )
        except PlaywrightTimeoutError as exc:
            raise RetryableExecutionError(
                "Marketplace browser action timed out",
                error_text=str(exc),
                page_url=page_url,
                screenshot_path=screenshot_path,
                trace_reference=str(trace_path),
            ) from exc
        finally:
            try:
                context.tracing.stop(path=str(trace_path))
                trace_reference = str(trace_path)
            except Exception:
                trace_reference = trace_reference or None
            context.close()
            browser.close()


def _fill_if_visible(page, selectors: list[str], value: str) -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            locator.first.fill(value)
            return
    raise RetryableExecutionError(
        "Required Facebook login field not visible",
        error_text=f"Could not find any selector from: {selectors}",
        page_url=page.url,
    )


def _click_if_visible(page, selectors: list[str]) -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            locator.first.click()
            return
    raise RetryableExecutionError(
        "Required Facebook login button not visible",
        error_text=f"Could not find any selector from: {selectors}",
        page_url=page.url,
    )


def _login_form_still_visible(page) -> bool:
    for selector in ('input[name="email"]', 'input#email', 'input[name="pass"]', 'input#pass'):
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            return True
    return False


def _is_checkpoint(page) -> bool:
    url = page.url.lower()
    if "checkpoint" in url or "login/identify" in url or "two_step_verification" in url:
        return True
    body = page.content().lower()
    return any(
        token in body
        for token in (
            "checkpoint",
            "suspicious activity",
            "two-factor authentication",
            "approve your login",
            "confirm it was you",
        )
    )


def _extract_note_field(notes: str | None, key: str) -> str | None:
    if not notes:
        return None
    match = re.search(rf"\b{re.escape(key)}:\s*([^|]+)", notes)
    return match.group(1).strip() if match else None


def _claude_text(prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise WorkerExecutionError(
            "Claude CU fallback requires ANTHROPIC_API_KEY",
            error_text="ANTHROPIC_API_KEY not set — Claude CU fallback unavailable",
            engine="claude_cu",
        )
    payload = {
        "model": os.getenv("HUNTER_CLAUDE_MODEL", "claude-sonnet-4-5"),
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    chunks: list[str] = []
    for item in data.get("content", []):
        if item.get("type") == "text":
            chunks.append(item.get("text", ""))
    return "\n".join(chunk for chunk in chunks if chunk).strip()
