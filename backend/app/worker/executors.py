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
    if task_type == "government_portal_search":
        return _execute_government_portal_search(task, spec)
    if task_type == "intake_form_submission":
        return _execute_intake_form_submission(task, spec)
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


# Selector hints per identity field, shared by every executor that fills
# a form with Commander's stored identity data. Deliberately conservative:
# _fill_identity_fields_or_abort aborts the ENTIRE submission rather than
# guess when a field can't be confidently located — this is a data-
# integrity control (never file a claim/application with a missing or
# misattributed field), not a policy gate.
_IDENTITY_FIELD_SELECTOR_HINTS: dict[str, list[str]] = {
    "full_name": [
        'input[name*="fullname" i]', 'input[name*="full_name" i]', 'input[id*="fullname" i]',
        'input[placeholder*="full name" i]', 'input[placeholder*="your name" i]',
        'input[name="name"]', 'input[id="name"]',
    ],
    "email": ['input[type="email"]', 'input[name*="email" i]', 'input[id*="email" i]'],
    "phone": ['input[type="tel"]', 'input[name*="phone" i]', 'input[id*="phone" i]'],
    "dob": [
        'input[name*="dob" i]', 'input[name*="birthdate" i]', 'input[name*="date_of_birth" i]',
        'input[id*="dob" i]', 'input[type="date"]',
    ],
    "ssn": ['input[name*="ssn" i]', 'input[name*="social" i]', 'input[id*="ssn" i]'],
    "address_line1": [
        'input[name*="address1" i]', 'input[name*="addressline1" i]', 'input[name*="street" i]',
        'input[id*="address1" i]', 'input[autocomplete="address-line1"]',
    ],
    "address_line2": [
        'input[name*="address2" i]', 'input[name*="addressline2" i]',
        'input[id*="address2" i]', 'input[autocomplete="address-line2"]',
    ],
    "city": ['input[name*="city" i]', 'input[id*="city" i]', 'input[autocomplete="address-level2"]'],
    "state": [
        'select[name*="state" i]', 'input[name*="state" i]', 'select[id*="state" i]',
        'select[autocomplete="address-level1"]',
    ],
    "zip": [
        'input[name*="zip" i]', 'input[name*="postal" i]', 'input[id*="zip" i]',
        'input[autocomplete="postal-code"]',
    ],
}


def _fill_identity_fields_or_abort(
    page, task_id: str, identity_fields: dict[str, str], *, artifact_prefix: str
) -> list[str]:
    """Fills every provided identity field (name -> real value, sourced
    only from Commander's own stored data — never invented here). If any
    field's input can't be confidently located, aborts the WHOLE
    submission rather than filing an incomplete or misattributed
    claim/application. Returns the list of field NAMES filled — never
    values, so the caller can safely record what happened without ever
    logging or persisting the actual data."""
    filled: list[str] = []
    for field_name, value in identity_fields.items():
        selectors = _IDENTITY_FIELD_SELECTOR_HINTS.get(field_name)
        if not selectors:
            raise RetryableExecutionError(
                f"No selector pattern known for identity field '{field_name}' — aborting rather than guessing",
                error_text=f"unknown identity field: {field_name}",
                page_url=page.url,
            )
        located = False
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                tag = locator.first.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    locator.first.select_option(label=value)
                else:
                    locator.first.fill(value)
                located = True
                filled.append(field_name)
                break
        if not located:
            screenshot_path = str(_artifact_dir(task_id) / f"{artifact_prefix}-missing-field.png")
            page.screenshot(path=screenshot_path, full_page=True)
            raise RetryableExecutionError(
                f"Could not locate a form field for required identity field '{field_name}' — "
                "aborting the entire submission rather than filing it incomplete or misattributed",
                error_text=f"missing field: {field_name}",
                page_url=page.url,
                screenshot_path=screenshot_path,
            )
    return filled


def _assert_ssn_use_explicitly_approved(spec: dict[str, Any], identity_fields: dict[str, str]) -> None:
    """Commander's rule (2026-09-09): name, DOB, address, and email may be
    used autonomously; SSN requires Commander's explicit consent for that
    specific submission every time. This is a hard, code-level check —
    not a convention the dispatcher is trusted to honor — so a future
    dispatch mistake can't silently slip an SSN through. Refuses (does
    not retry) unless spec["ssn_explicitly_approved"] is True."""
    if "ssn" in identity_fields and not spec.get("ssn_explicitly_approved"):
        raise WorkerExecutionError(
            "SSN present in identity_fields without explicit per-submission Commander "
            "consent — refusing rather than submitting it",
            escalation_type="commander_boundary",
            error_text="ssn_explicitly_approved must be true for this specific task to include ssn",
        )


def _execute_government_portal_search(task: dict[str, Any], spec: dict[str, Any]) -> WorkerResult:
    """
    Search a public government lookup portal for a Commander-supplied
    business name. Search-only by default. If spec["identity_fields"] is
    supplied (populated only from Commander's own stored identity data —
    see app/services/commander_identity.py — never invented), and a
    matching record's claim link is found on the results page, continues
    in the same session to fill and submit the claim; any required field
    that can't be confidently located aborts the whole submission rather
    than guessing (see _fill_identity_fields_or_abort).
    """
    task_id = task.get("task_id")
    search_url = spec.get("search_url")
    business_name = spec.get("business_name")
    if not search_url or not business_name:
        raise WorkerExecutionError(
            "Missing search_url or business_name in task spec",
            escalation_type="unrecoverable_failure",
            error_text=f"spec={spec}",
        )

    screenshot_path: str | None = None
    page_url: str | None = None
    trace_reference: str | None = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=os.getenv("HUNTER_PLAYWRIGHT_HEADLESS", "true").lower() != "false",
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()
        trace_path = _artifact_dir(task_id) / "gov-portal-trace.zip"
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page_url = page.url
            page.wait_for_timeout(2000)

            if _has_anti_bot_challenge(page):
                screenshot_path = str(_artifact_dir(task_id) / "gov-portal-antibot.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Anti-bot control detected on government portal — Hunter does not bypass these",
                    escalation_type="commander_boundary",
                    error_text="CAPTCHA/anti-bot indicator found before search",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            _fill_if_visible(
                page,
                [
                    'input[name*="BusinessName" i]', 'input[id*="BusinessName" i]',
                    'input[name*="EntityName" i]', 'input[id*="EntityName" i]',
                    'input[placeholder*="Business" i]', 'input[placeholder*="Company" i]',
                    'input[name*="LastName" i]', 'input[id*="LastName" i]',
                    'input[placeholder*="Name" i]',
                ],
                business_name,
                field_description="Business/entity name search field",
            )
            _click_if_visible(
                page,
                ['button:has-text("Search")', 'input[type="submit"][value*="Search" i]', 'button[type="submit"]'],
                field_description="Search submit button",
            )
            page.wait_for_timeout(4000)
            page_url = page.url

            if _has_anti_bot_challenge(page):
                screenshot_path = str(_artifact_dir(task_id) / "gov-portal-antibot-postsearch.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Anti-bot control appeared after search submission",
                    escalation_type="commander_boundary",
                    error_text="CAPTCHA/anti-bot indicator found on results page",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            screenshot_path = str(_artifact_dir(task_id) / "gov-portal-results.png")
            page.screenshot(path=screenshot_path, full_page=True)
            result_text = page.locator("body").inner_text()

            identity_fields: dict[str, str] = spec.get("identity_fields") or {}
            _assert_ssn_use_explicitly_approved(spec, identity_fields)
            if not identity_fields:
                outcome = {
                    "search_performed": True,
                    "business_name_searched": business_name,
                    "claim_filed": False,
                    "result_excerpt": result_text[:1000],
                }
                return WorkerResult(
                    outcome=outcome,
                    notes=(
                        "Hosted HVA searched the government unclaimed-property portal for the "
                        "Commander-supplied business name. Search only — no identity fields "
                        "were on file yet, so no claim was attempted."
                    ),
                    engine="playwright",
                    page_url=page_url,
                    screenshot_path=screenshot_path,
                    trace_reference=str(trace_path),
                )

            claim_trigger = page.locator(
                'a:has-text("File a Claim"), a:has-text("File Claim"), a:has-text("Start Claim"), '
                'button:has-text("File a Claim"), button:has-text("Start Claim"), a:has-text("Claim")'
            )
            if claim_trigger.count() == 0 or not claim_trigger.first.is_visible():
                # Honest, legitimate outcome — no claimable record found on this
                # search. Not a failure: nothing to file.
                outcome = {
                    "search_performed": True,
                    "business_name_searched": business_name,
                    "claim_filed": False,
                    "claim_available": False,
                    "result_excerpt": result_text[:1000],
                }
                return WorkerResult(
                    outcome=outcome,
                    notes="Hosted HVA searched the portal; no claimable matching record was found.",
                    engine="playwright",
                    page_url=page_url,
                    screenshot_path=screenshot_path,
                    trace_reference=str(trace_path),
                )

            claim_trigger.first.click()
            page.wait_for_timeout(2000)
            page_url = page.url

            if _has_anti_bot_challenge(page):
                screenshot_path = str(_artifact_dir(task_id) / "gov-portal-antibot-claim.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Anti-bot control appeared on the claim-filing page",
                    escalation_type="commander_boundary",
                    error_text="CAPTCHA/anti-bot indicator found before claim submission",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            fields_filled = _fill_identity_fields_or_abort(
                page, task_id, identity_fields, artifact_prefix="gov-portal-claim"
            )

            submit_button = page.locator(
                'button:has-text("Submit"), input[type="submit"][value*="Submit" i], button[type="submit"]'
            )
            if submit_button.count() == 0 or not submit_button.first.is_visible():
                raise RetryableExecutionError(
                    "Could not locate a final submit control for the claim — aborting rather than guessing",
                    page_url=page.url,
                )
            submit_button.first.click()
            page.wait_for_timeout(3000)
            confirmation_url = page.url
            confirmation_screenshot = str(_artifact_dir(task_id) / "gov-portal-claim-confirmation.png")
            page.screenshot(path=confirmation_screenshot, full_page=True)

            outcome = {
                "search_performed": True,
                "business_name_searched": business_name,
                "claim_filed": True,
                "fields_filled": fields_filled,  # field NAMES only, never values
            }
            return WorkerResult(
                outcome=outcome,
                notes=(
                    "Hosted HVA found a matching unclaimed-property record and filed the claim "
                    "using Commander's stored identity fields."
                ),
                engine="playwright",
                page_url=confirmation_url,
                screenshot_path=confirmation_screenshot,
                trace_reference=str(trace_path),
            )
        except PlaywrightTimeoutError as exc:
            raise RetryableExecutionError(
                "Government portal search timed out",
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


def _execute_intake_form_submission(task: dict[str, Any], spec: dict[str, Any]) -> WorkerResult:
    """
    Navigates directly to an intake/signup form (e.g. a law firm's case
    intake page) and fills + submits it using Commander's stored
    identity fields (spec["identity_fields"], sourced only from
    app/services/commander_identity.py — never invented). Same
    fail-closed rule as claim submission: any required field that can't
    be confidently located aborts the whole submission rather than
    filing it incomplete or misattributed.
    """
    task_id = task.get("task_id")
    intake_url = spec.get("intake_url")
    identity_fields: dict[str, str] = spec.get("identity_fields") or {}
    if not intake_url or not identity_fields:
        raise WorkerExecutionError(
            "Missing intake_url or identity_fields in task spec",
            escalation_type="unrecoverable_failure",
            error_text=f"spec keys={list(spec.keys())}",
        )
    _assert_ssn_use_explicitly_approved(spec, identity_fields)

    screenshot_path: str | None = None
    page_url: str | None = None
    trace_reference: str | None = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=os.getenv("HUNTER_PLAYWRIGHT_HEADLESS", "true").lower() != "false",
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()
        trace_path = _artifact_dir(task_id) / "intake-trace.zip"
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page.goto(intake_url, wait_until="domcontentloaded", timeout=60000)
            page_url = page.url
            page.wait_for_timeout(2000)

            if _has_anti_bot_challenge(page):
                screenshot_path = str(_artifact_dir(task_id) / "intake-antibot.png")
                page.screenshot(path=screenshot_path, full_page=True)
                raise WorkerExecutionError(
                    "Anti-bot control detected on intake form — Hunter does not bypass these",
                    escalation_type="commander_boundary",
                    error_text="CAPTCHA/anti-bot indicator found before submission",
                    page_url=page.url,
                    screenshot_path=screenshot_path,
                )

            fields_filled = _fill_identity_fields_or_abort(page, task_id, identity_fields, artifact_prefix="intake")

            submit_button = page.locator(
                'button:has-text("Submit"), button:has-text("Send"), '
                'input[type="submit"], button[type="submit"]'
            )
            if submit_button.count() == 0 or not submit_button.first.is_visible():
                raise RetryableExecutionError(
                    "Could not locate a submit control for the intake form — aborting rather than guessing",
                    page_url=page.url,
                )
            submit_button.first.click()
            page.wait_for_timeout(3000)
            confirmation_url = page.url
            confirmation_screenshot = str(_artifact_dir(task_id) / "intake-confirmation.png")
            page.screenshot(path=confirmation_screenshot, full_page=True)

            outcome = {
                "submitted": True,
                "fields_filled": fields_filled,  # field NAMES only, never values
            }
            return WorkerResult(
                outcome=outcome,
                notes="Hosted HVA submitted the intake form using Commander's stored identity fields.",
                engine="playwright",
                page_url=confirmation_url,
                screenshot_path=confirmation_screenshot,
                trace_reference=str(trace_path),
            )
        except PlaywrightTimeoutError as exc:
            raise RetryableExecutionError(
                "Intake form submission timed out",
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


def _fill_if_visible(page, selectors: list[str], value: str, *, field_description: str = "Required form field") -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            locator.first.fill(value)
            return
    raise RetryableExecutionError(
        f"{field_description} not visible",
        error_text=f"Could not find any selector from: {selectors}",
        page_url=page.url,
    )


def _click_if_visible(page, selectors: list[str], *, field_description: str = "Required button") -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            locator.first.click()
            return
    raise RetryableExecutionError(
        f"{field_description} not visible",
        error_text=f"Could not find any selector from: {selectors}",
        page_url=page.url,
    )


def _has_anti_bot_challenge(page) -> bool:
    """Conservative CAPTCHA/anti-bot detector. Hunter does not attempt to
    solve or bypass these — if found, the task escalates instead."""
    for selector in (
        'iframe[src*="recaptcha" i]', '.g-recaptcha', 'iframe[src*="hcaptcha" i]',
        '[class*="hcaptcha" i]', 'iframe[title*="captcha" i]', '[id*="captcha" i]',
    ):
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    body = page.content().lower()
    return any(
        token in body
        for token in ("verify you are human", "i'm not a robot", "captcha", "bot detection", "access denied")
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
