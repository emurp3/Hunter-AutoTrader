"""
The OBSERVE -> REASON -> ACT -> VERIFY fallback (app/worker/execution_reasoning.py)
is Hunter's own reasoning capability for when deterministic Playwright
selectors can't find what they're looking for — replacing "Claude reads
Render logs and patches a selector" with Hunter observing the real page
itself and deciding its own next step, using its existing operational
advisor route (Grok/Venice/DeepSeek — see llm_client.py), never the
separate Hunter AI chat model.

Two scenarios are tested end-to-end: GOOGLE-02's real shape (a marketing
landing page with no form, requiring one click-through to a real intake
page) and a second, structurally different multi-step wizard (landing ->
qualifying question -> intake form) — proving the mechanism is generic
code, not GOOGLE-02 logic disguised as something reusable.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from app.worker.execution_reasoning import (
    _fingerprint,
    _looks_like_identity_value,
    act_on_decision,
    observe_page_state,
    run_observe_reason_act_verify,
)
import app.worker.execution_reasoning as reasoning_mod


class _FakeLocator:
    def __init__(self, el: dict | None, page: "_FakePage"):
        self.el = el
        self.page = page

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1 if self.el else 0

    def click(self, timeout: int | None = None) -> None:
        effect = self.page.click_effects.get(self.el["ref"])
        if effect:
            effect(self.page)

    def select_option(self, label: str) -> None:
        self.el["selected"] = label

    def fill(self, value: str) -> None:
        self.el["filled"] = value


class _FakeContext:
    """Stands in for Playwright's BrowserContext — just enough for
    act_on_decision's new-tab detection (`len(page.context.pages)`)."""

    def __init__(self, pages: list):
        self.pages = pages


class _FakePage:
    """A single-frame fake page whose 'DOM' is a plain list of element
    dicts the test controls directly, plus click_effects that let a test
    simulate real navigation (new URL/title/elements) when a specific
    ref is clicked — the same shape run_observe_reason_act_verify sees
    from a real Playwright page, without needing a browser."""

    def __init__(self, elements, *, url: str, title: str, body_text: str = ""):
        self.elements = elements
        self.url = url
        self.title_text = title
        self.body_text = body_text
        self.frames = [self]
        self.click_effects: dict[str, callable] = {}
        self.wait_calls = 0
        self.context = _FakeContext([self])

    def evaluate(self, js: str):
        if "data-hunter-ref" in js:
            return [dict(e) for e in self.elements]
        return self.body_text

    def title(self) -> str:
        return self.title_text

    def locator(self, selector: str) -> _FakeLocator:
        m = re.search(r'data-hunter-ref="(\d+)"', selector)
        if not m:
            return _FakeLocator(None, self)
        idx = m.group(1)
        for e in self.elements:
            if e["ref"] == idx:
                return _FakeLocator(e, self)
        return _FakeLocator(None, self)

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls += 1


def _navigate(page: _FakePage, *, url: str, title: str, elements, body_text: str = "") -> None:
    page.url = url
    page.title_text = title
    page.elements = elements
    page.body_text = body_text


# ---------------------------------------------------------------------
# OBSERVE
# ---------------------------------------------------------------------

def test_observe_captures_real_page_state_with_frame_prefixed_refs():
    page = _FakePage(
        [{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {"href": "https://x.com/contact"}}],
        url="https://x.com/landing", title="Landing Page", body_text="Welcome",
    )

    obs = observe_page_state(page, objective="find the intake form", checkpoint="none yet")

    assert obs["url"] == "https://x.com/landing"
    assert obs["title"] == "Landing Page"
    assert obs["objective"] == "find the intake form"
    assert obs["interactive_elements"][0]["ref"] == "f0_0"
    assert obs["interactive_elements"][0]["text"] == "Contact Us"


def test_observe_never_includes_identity_data():
    page = _FakePage([], url="https://x.com", title="X", body_text="public marketing copy only")
    obs = observe_page_state(page, objective="obj", checkpoint="chk")
    serialized = str(obs)
    assert "123-45-6789" not in serialized  # nothing resembling an SSN could appear — nothing was ever given


def test_fingerprint_stable_for_identical_state_different_for_changed_state():
    page1 = _FakePage([{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {}}], url="https://x.com", title="X")
    page2 = _FakePage([{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {}}], url="https://x.com", title="X")
    page3 = _FakePage([{"ref": "0", "tag": "a", "text": "Something Else", "attrs": {}}], url="https://x.com", title="X")

    obs1 = observe_page_state(page1, objective="o", checkpoint="c")
    obs2 = observe_page_state(page2, objective="o", checkpoint="c")
    obs3 = observe_page_state(page3, objective="o", checkpoint="c")

    assert _fingerprint(obs1) == _fingerprint(obs2)
    assert _fingerprint(obs1) != _fingerprint(obs3)


# ---------------------------------------------------------------------
# ACT
# ---------------------------------------------------------------------

def test_act_click_resolves_ref_and_invokes_click_effect():
    page = _FakePage([{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {}}], url="https://x.com", title="X")
    clicked = {"happened": False}
    page.click_effects["0"] = lambda p: clicked.__setitem__("happened", True)

    result = act_on_decision(page, {"action": "click", "ref": "f0_0"}, identity_fields={})

    assert result["progressed"] is True
    assert clicked["happened"] is True


def test_act_click_that_opens_a_new_tab_is_detected_and_returned():
    """Live tonight: a link to a genuinely different domain (a marketing
    site handing off to a separate intake platform) opened in a new tab,
    which the code wasn't watching for — it kept observing the original,
    now-irrelevant tab. act_on_decision must detect a new page appearing
    in the browser context after a click and hand it back."""
    main_page = _FakePage([{"ref": "0", "tag": "a", "text": "Start Questionnaire", "attrs": {}}], url="https://x.com", title="X")
    new_tab = _FakePage([], url="https://intake.example.com", title="Intake")

    def _open_new_tab(p: _FakePage) -> None:
        p.context.pages.append(new_tab)

    main_page.click_effects["0"] = _open_new_tab

    result = act_on_decision(main_page, {"action": "click", "ref": "f0_0"}, identity_fields={})

    assert result["progressed"] is True
    assert result["new_page"] is new_tab


def test_act_click_with_no_new_tab_does_not_report_one():
    page = _FakePage([{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {}}], url="https://x.com", title="X")
    page.click_effects["0"] = lambda p: None  # ordinary same-tab click, nothing opens

    result = act_on_decision(page, {"action": "click", "ref": "f0_0"}, identity_fields={})

    assert result["progressed"] is True
    assert "new_page" not in result


def test_act_give_up_never_touches_the_page():
    page = _FakePage([], url="https://x.com", title="X")
    result = act_on_decision(page, {"action": "give_up", "reasoning": "CAPTCHA present"}, identity_fields={})
    assert result == {"progressed": False, "give_up": True, "reasoning": "CAPTCHA present"}


def test_act_handoff_to_identity_fill_returns_handoff_flag():
    page = _FakePage([], url="https://x.com", title="X")
    result = act_on_decision(page, {"action": "handoff_to_identity_fill"}, identity_fields={"full_name": "Eddie Murphy Jr."})
    assert result["handoff_to_identity_fill"] is True
    assert result["progressed"] is False


def test_act_refuses_fill_value_matching_real_identity_data():
    page = _FakePage([{"ref": "0", "tag": "select", "text": "State", "attrs": {}}], url="https://x.com", title="X")
    identity_fields = {"full_name": "Eddie Murphy Jr.", "email": "eddie@example.com"}

    result = act_on_decision(
        page, {"action": "fill", "ref": "f0_0", "value": "eddie@example.com"}, identity_fields=identity_fields
    )

    assert result["progressed"] is False
    assert "refused" in result["error"]


def test_act_unknown_ref_reports_error_not_exception():
    page = _FakePage([], url="https://x.com", title="X")
    result = act_on_decision(page, {"action": "click", "ref": "f0_99"}, identity_fields={})
    assert result["progressed"] is False
    assert "not found" in result["error"]


def test_looks_like_identity_value_case_insensitive():
    identity_fields = {"email": "Eddie@Example.com"}
    assert _looks_like_identity_value("eddie@example.com", identity_fields) is True
    assert _looks_like_identity_value("Continue as Guest", identity_fields) is False
    assert _looks_like_identity_value(None, identity_fields) is False


# ---------------------------------------------------------------------
# run_observe_reason_act_verify — full loop
# ---------------------------------------------------------------------

def test_no_advisor_configured_returns_failure_without_calling_reason(monkeypatch):
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: False)
    mock_reason = MagicMock()
    monkeypatch.setattr(reasoning_mod, "call_llm_json", mock_reason)

    page = _FakePage([], url="https://x.com", title="X")
    result = run_observe_reason_act_verify(
        page, objective="find the form", identity_fields={}, client=MagicMock()
    )

    assert result["success"] is False
    assert result["trace"][0]["outcome"] == "no_advisor_configured"
    mock_reason.assert_not_called()


def test_no_advisor_response_stops_cleanly(monkeypatch):
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)
    monkeypatch.setattr(reasoning_mod, "call_llm_json", lambda *a, **k: None)

    page = _FakePage([{"ref": "0", "tag": "a", "text": "Contact", "attrs": {}}], url="https://x.com", title="X")
    result = run_observe_reason_act_verify(
        page, objective="find the form", identity_fields={}, client=MagicMock()
    )

    assert result["success"] is False
    assert result["trace"][-1]["outcome"] == "no_advisor_response"


def test_llm_give_up_stops_without_looping(monkeypatch):
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)
    monkeypatch.setattr(
        reasoning_mod, "call_llm_json",
        lambda *a, **k: {"action": "give_up", "ref": None, "value": None, "reasoning": "CAPTCHA blocks progress"},
    )

    page = _FakePage([], url="https://x.com", title="X")
    result = run_observe_reason_act_verify(
        page, objective="find the form", identity_fields={}, client=MagicMock()
    )

    assert result["success"] is False
    assert result["trace"][-1]["outcome"] == "llm_gave_up"
    assert len(result["trace"]) == 1


def test_repeated_identical_state_stops_early_instead_of_looping(monkeypatch):
    """A decision that clicks something but changes nothing (e.g. a
    dead link) must not spin for max_iterations — the fingerprint
    doesn't change, so the loop recognizes no progress and gives up."""
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)
    call_count = {"n": 0}

    def _fake_reason(*a, **k):
        call_count["n"] += 1
        return {"action": "click", "ref": "f0_0", "value": None, "reasoning": "try the link again"}

    monkeypatch.setattr(reasoning_mod, "call_llm_json", _fake_reason)

    page = _FakePage([{"ref": "0", "tag": "a", "text": "Dead Link", "attrs": {}}], url="https://x.com", title="X")
    # No click_effects registered — clicking "0" changes nothing.

    result = run_observe_reason_act_verify(
        page, objective="find the form", identity_fields={}, client=MagicMock(), max_iterations=5
    )

    assert result["success"] is False
    assert result["trace"][-1]["outcome"] == "no_progress_repeated_state"
    # Stopped on the second observation (fingerprint repeats), not after
    # burning all 5 iterations.
    assert call_count["n"] < 5


def test_max_iterations_reached_bounds_the_loop(monkeypatch):
    """Each iteration genuinely changes the page (so no-progress detection
    doesn't trigger) but never reaches a resolution — the iteration cap
    is the only thing that stops it."""
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)

    def _fake_reason(*a, **k):
        # Always propose clicking whatever's on the (ever-changing) page.
        return {"action": "click", "ref": "f0_0", "value": None, "reasoning": "keep moving"}

    monkeypatch.setattr(reasoning_mod, "call_llm_json", _fake_reason)

    page = _FakePage([{"ref": "0", "tag": "a", "text": "Next 0", "attrs": {}}], url="https://x.com/0", title="Step 0")
    counter = {"n": 0}

    def _advance(p: _FakePage) -> None:
        counter["n"] += 1
        _navigate(
            p, url=f"https://x.com/{counter['n']}", title=f"Step {counter['n']}",
            elements=[{"ref": "0", "tag": "a", "text": f"Next {counter['n']}", "attrs": {}}],
        )
        p.click_effects["0"] = _advance

    page.click_effects["0"] = _advance

    result = run_observe_reason_act_verify(
        page, objective="find the form", identity_fields={}, client=MagicMock(), max_iterations=3
    )

    assert result["success"] is False
    assert result["trace"][-1]["outcome"] == "max_iterations_reached"


def test_click_opening_a_new_tab_switches_the_loops_working_page(monkeypatch):
    """The exact real scenario found live tonight: the LLM correctly
    identifies that the intake form lives on a completely different
    domain and clicks the link — but that link opens a new tab. The loop
    must switch to observing/acting on the new tab, not keep re-checking
    the original page (which would look like 'no progress' forever)."""
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)

    responses = iter([
        {"action": "click", "ref": "f0_0", "value": None,
         "reasoning": "The only path to the intake questionnaire is the link to the separate intake platform."},
        {"action": "handoff_to_identity_fill", "ref": None, "value": None,
         "reasoning": "The real form is now visible on this new tab."},
    ])
    monkeypatch.setattr(reasoning_mod, "call_llm_json", lambda *a, **k: next(responses))

    main_page = _FakePage(
        [{"ref": "0", "tag": "a", "text": "Start Your Claim", "attrs": {"href": "https://intake-platform.example.com"}}],
        url="https://marketing-site.example.com/lawsuit", title="Marketing Page",
    )
    new_tab = _FakePage(
        [{"ref": "0", "tag": "input", "text": "", "attrs": {"name": "fullname", "type": "text"}}],
        url="https://intake-platform.example.com/form", title="Case Intake",
    )
    main_page.click_effects["0"] = lambda p: p.context.pages.append(new_tab)

    result = run_observe_reason_act_verify(
        main_page, objective="reach the intake form", identity_fields={"full_name": "Eddie Murphy Jr."},
        client=MagicMock(),
    )

    assert result["success"] is True
    assert result["trace"][-1]["outcome"] == "handoff_to_identity_fill"
    # The loop's working page switched to the new tab — the caller must
    # continue on this page, not the original marketing page.
    assert result["page"] is new_tab


# ---------------------------------------------------------------------
# Acceptance-style scenarios: GOOGLE-02's real shape, and a second,
# structurally different multi-step wizard — same generic code, zero
# candidate-specific logic.
# ---------------------------------------------------------------------

def test_google02_shaped_scenario_landing_page_click_through_to_handoff(monkeypatch):
    """Mirrors what was actually found live: a marketing landing page
    with no form fields, a 'Contact Us' link, and the real intake form
    one click away. The LLM is mocked to behave the way a real model
    reasoning over this exact observation would: click Contact Us, then
    hand off once personal-info fields are visible."""
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)

    responses = iter([
        {"action": "click", "ref": "f0_0", "value": None, "reasoning": "No form here; Contact Us likely has it"},
        {"action": "handoff_to_identity_fill", "ref": None, "value": None, "reasoning": "Name/email fields now visible"},
    ])
    monkeypatch.setattr(reasoning_mod, "call_llm_json", lambda *a, **k: next(responses))

    page = _FakePage(
        [{"ref": "0", "tag": "a", "text": "Contact Us", "attrs": {"href": "https://potterhandy.com/contact"}}],
        url="https://potterhandy.com/google-privacy-violations-lawsuit/",
        title="Google Incognito Mode Lawsuit | Class Action for Privacy Violations",
        body_text="Skip to content ... CONTACT US ...",
    )

    def _go_to_contact_page(p: _FakePage) -> None:
        _navigate(
            p, url="https://potterhandy.com/contact", title="Contact Potter Handy",
            elements=[
                {"ref": "0", "tag": "input", "text": "", "attrs": {"name": "your-name", "type": "text"}},
                {"ref": "1", "tag": "input", "text": "", "attrs": {"name": "your-email", "type": "email"}},
            ],
            body_text="Contact our firm",
        )

    page.click_effects["0"] = _go_to_contact_page

    result = run_observe_reason_act_verify(
        page, objective="Find and reach the actual case-intake form", identity_fields={"full_name": "Eddie Murphy Jr."},
        client=MagicMock(),
    )

    assert result["success"] is True
    assert result["trace"][-1]["outcome"] == "handoff_to_identity_fill"
    assert page.url == "https://potterhandy.com/contact"


def test_second_structurally_different_opportunity_multistep_wizard(monkeypatch):
    """A DIFFERENT shape entirely: a landing page -> a qualifying
    question (select an option, no personal data) -> the real intake
    form. No GOOGLE-02-specific text, URL, or logic anywhere in this
    test or in execution_reasoning.py — same generic function handles
    an unrelated multi-step flow, proving it's a reusable capability."""
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: True)

    responses = iter([
        {"action": "click", "ref": "f0_0", "value": None, "reasoning": "Start the eligibility flow"},
        {"action": "select", "ref": "f0_0", "value": "Yes, within the last 4 years",
         "reasoning": "Answering the qualifying question using its own listed option"},
        {"action": "handoff_to_identity_fill", "ref": None, "value": None, "reasoning": "Personal info form now visible"},
    ])
    monkeypatch.setattr(reasoning_mod, "call_llm_json", lambda *a, **k: next(responses))

    page = _FakePage(
        [{"ref": "0", "tag": "button", "text": "Check My Eligibility", "attrs": {}}],
        url="https://example-recall-claims.com/",
        title="Product Recall Compensation Program",
        body_text="Millions may qualify for compensation.",
    )

    def _show_qualifying_question(p: _FakePage) -> None:
        _navigate(
            p, url="https://example-recall-claims.com/eligibility", title="Eligibility Check",
            elements=[{
                "ref": "0", "tag": "select", "text": "When did you purchase the product?",
                "attrs": {"options": ["Yes, within the last 4 years", "No, longer ago", "Not sure"]},
            }],
            body_text="When did you purchase the affected product?",
        )
        p.click_effects.pop("0", None)

    def _show_intake_form(p: _FakePage) -> None:
        _navigate(
            p, url="https://example-recall-claims.com/claim-form", title="Submit Your Claim",
            elements=[
                {"ref": "0", "tag": "input", "text": "", "attrs": {"name": "claimant_full_name", "type": "text"}},
                {"ref": "1", "tag": "input", "text": "", "attrs": {"name": "claimant_email", "type": "email"}},
            ],
            body_text="Enter your details to submit your claim",
        )

    page.click_effects["0"] = _show_qualifying_question

    original_act = reasoning_mod.act_on_decision

    def _act_with_followup(page_arg, decision, identity_fields):
        result = original_act(page_arg, decision, identity_fields)
        if decision.get("action") == "select" and result.get("progressed"):
            _show_intake_form(page_arg)
        return result

    monkeypatch.setattr(reasoning_mod, "act_on_decision", _act_with_followup)

    result = run_observe_reason_act_verify(
        page, objective="Find and reach the real claim submission form",
        identity_fields={"full_name": "Eddie Murphy Jr.", "email": "eddie@example.com"},
        client=MagicMock(),
    )

    assert result["success"] is True
    assert result["trace"][-1]["outcome"] == "handoff_to_identity_fill"
    assert page.url == "https://example-recall-claims.com/claim-form"
    # No opportunity-specific string from this test leaked into the
    # module under test — it's the exact same function used above for a
    # completely different page shape and objective.
    import inspect
    source = inspect.getsource(reasoning_mod)
    assert "potterhandy" not in source
    assert "recall-claims" not in source
    assert "GOOGLE-02" not in source
