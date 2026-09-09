"""
_fill_identity_fields_with_reasoning_fallback (app/worker/executors.py) is
the glue between the deterministic fast path (_fill_identity_fields_or_abort)
and the OBSERVE->REASON->ACT->VERIFY fallback (app/worker/execution_reasoning.py).
Commander's requirement: the deterministic path stays first and cheap —
no LLM call unless it genuinely can't find a field — and the fallback
never invents success on its own.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import app.worker.execution_reasoning as reasoning_mod
from app.worker.executors import (
    RetryableExecutionError,
    _fill_identity_fields_with_reasoning_fallback,
)


class _FakeLocator:
    def __init__(self, present: bool, is_select: bool = False):
        self._present = present
        self.is_select = is_select
        self.filled_value: str | None = None

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1 if self._present else 0

    def is_visible(self) -> bool:
        return True

    def evaluate(self, _js: str) -> str:
        return "select" if self.is_select else "input"

    def fill(self, value: str) -> None:
        self.filled_value = value

    def select_option(self, label: str) -> None:
        self.filled_value = label


class _FakePage:
    def __init__(self, present_selectors: set[str]):
        self._present = present_selectors
        self.locators: dict[str, _FakeLocator] = {}
        self.url = "https://example.com/intake"
        self.screenshots: list[str] = []
        self.frames = [self]

    def locator(self, selector: str) -> _FakeLocator:
        # Not cached: presence is re-checked against the live `_present`
        # set every call, since a test may mutate it mid-flow (simulating
        # navigation) between the deterministic pass and the retry.
        loc = _FakeLocator(present=selector in self._present)
        self.locators[selector] = loc
        return loc

    def screenshot(self, path: str, full_page: bool = True) -> None:
        self.screenshots.append(path)

    def evaluate(self, _js: str):
        return []

    def title(self) -> str:
        return "Fake Page"


_FULL_NAME_SELECTOR = 'input[name*="fullname" i]'


def test_fast_path_succeeds_without_ever_invoking_the_llm(monkeypatch):
    """The whole point of 'deterministic stays first' — if the plain
    selector match works, run_observe_reason_act_verify must never even
    be imported/called."""
    page = _FakePage({_FULL_NAME_SELECTOR})
    mock_reasoning_loop = MagicMock()
    monkeypatch.setattr(reasoning_mod, "run_observe_reason_act_verify", mock_reasoning_loop)

    filled, active_page = _fill_identity_fields_with_reasoning_fallback(
        page, "task-1", {"full_name": "Eddie Murphy Jr."},
        artifact_prefix="intake", objective="find the form",
    )

    assert filled == ["full_name"]
    assert active_page is page
    mock_reasoning_loop.assert_not_called()


def test_fallback_invoked_on_deterministic_failure_and_retry_succeeds(monkeypatch):
    """Deterministic fill fails first (field truly not present yet). The
    reasoning loop is mocked to 'succeed' by adding the field to the
    page (simulating it navigated somewhere the field now exists) —
    proving the wrapper retries the cheap deterministic path afterward
    rather than trying to fill anything itself."""
    page = _FakePage(set())  # nothing present yet

    def _fake_loop(page_arg, *, objective, identity_fields, client):
        page_arg._present.add(_FULL_NAME_SELECTOR)  # simulate having navigated to the real form
        return {"success": True, "trace": [{"outcome": "handoff_to_identity_fill"}]}

    monkeypatch.setattr(reasoning_mod, "run_observe_reason_act_verify", _fake_loop)

    filled, active_page = _fill_identity_fields_with_reasoning_fallback(
        page, "task-2", {"full_name": "Eddie Murphy Jr."},
        artifact_prefix="intake", objective="find the form",
    )

    assert filled == ["full_name"]
    assert active_page is page


def test_fallback_failure_raises_original_style_error_with_trace(monkeypatch):
    page = _FakePage(set())

    def _fake_loop(page_arg, *, objective, identity_fields, client):
        return {"success": False, "trace": [{"outcome": "llm_gave_up", "reasoning": "CAPTCHA present"}]}

    monkeypatch.setattr(reasoning_mod, "run_observe_reason_act_verify", _fake_loop)

    try:
        _fill_identity_fields_with_reasoning_fallback(
            page, "task-3", {"full_name": "Eddie Murphy Jr."},
            artifact_prefix="intake", objective="find the form",
        )
        assert False, "expected RetryableExecutionError"
    except RetryableExecutionError as exc:
        assert "full_name" in str(exc)
        assert "reasoning fallback also found no path forward" in str(exc)
        assert "llm_gave_up" in str(exc)


def test_no_advisor_configured_falls_through_to_the_same_original_error(monkeypatch):
    """If Hunter has no configured advisor at all, the reasoning loop
    itself reports that immediately (see execution_reasoning.py) — the
    wrapper must still surface a clean, informative failure rather than
    crash."""
    page = _FakePage(set())
    monkeypatch.setattr(reasoning_mod, "any_advisor_configured", lambda: False)

    try:
        _fill_identity_fields_with_reasoning_fallback(
            page, "task-4", {"full_name": "Eddie Murphy Jr."},
            artifact_prefix="intake", objective="find the form",
        )
        assert False, "expected RetryableExecutionError"
    except RetryableExecutionError as exc:
        assert "no_advisor_configured" in str(exc)
