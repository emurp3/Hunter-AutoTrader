"""
Found live tonight: GOOGLE-02's real intake page has no single "full name"
input, so every full_name selector hint missed and the executor correctly
aborted the whole submission rather than guess. Many intake forms split
the name into separate first/last fields instead of one combined field —
_fill_identity_fields_or_abort now falls back to filling first_name +
last_name (split on the first space) when no combined full_name field can
be found, before giving up.
"""

from __future__ import annotations

import pytest

from app.worker.executors import (
    RetryableExecutionError,
    _describe_visible_form_fields,
    _fill_identity_fields_or_abort,
)

_FULL_NAME_SELECTOR = 'input[name*="fullname" i]'
_FIRST_NAME_SELECTOR = 'input[name*="firstname" i]'
_LAST_NAME_SELECTOR = 'input[name*="lastname" i]'


class _FakeLocator:
    def __init__(self, present: bool, is_select: bool = False):
        self._present = present
        self.is_select = is_select
        self.filled_value: str | None = None
        self.selected_label: str | None = None

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
        self.selected_label = label


class _FakePage:
    """Maps exact selector strings (as they appear in
    _IDENTITY_FIELD_SELECTOR_HINTS) to whether a matching element exists —
    a stand-in for the real DOM Playwright would otherwise query."""

    def __init__(self, present_selectors: dict[str, str]):
        self._present = present_selectors
        self.locators: dict[str, _FakeLocator] = {}
        self.url = "https://example.com/intake"
        self.screenshots: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        loc = self.locators.get(selector)
        if loc is None:
            loc = _FakeLocator(present=selector in self._present)
            self.locators[selector] = loc
        return loc

    def screenshot(self, path: str, full_page: bool = True) -> None:
        self.screenshots.append(path)

    def evaluate(self, _js: str):
        return ["input[type=text](name=case_notes,id=notes)"]


def test_falls_back_to_first_last_name_when_no_combined_field_exists():
    page = _FakePage({_FIRST_NAME_SELECTOR: "x", _LAST_NAME_SELECTOR: "x"})

    filled = _fill_identity_fields_or_abort(
        page, "task-1", {"full_name": "Eddie Murphy Jr."}, artifact_prefix="intake"
    )

    assert filled == ["full_name"]
    assert page.locator(_FIRST_NAME_SELECTOR).filled_value == "Eddie"
    assert page.locator(_LAST_NAME_SELECTOR).filled_value == "Murphy Jr."


def test_uses_combined_field_directly_when_present_no_fallback_needed():
    page = _FakePage({_FULL_NAME_SELECTOR: "x"})

    filled = _fill_identity_fields_or_abort(
        page, "task-2", {"full_name": "Eddie Murphy Jr."}, artifact_prefix="intake"
    )

    assert filled == ["full_name"]
    assert page.locator(_FULL_NAME_SELECTOR).filled_value == "Eddie Murphy Jr."
    # Fallback fields were never even queried for a value, since the
    # combined field matched first.
    assert _FIRST_NAME_SELECTOR not in page.locators or page.locators[_FIRST_NAME_SELECTOR].filled_value is None


def test_aborts_when_neither_combined_nor_split_fields_exist():
    page = _FakePage({})

    with pytest.raises(RetryableExecutionError) as exc_info:
        _fill_identity_fields_or_abort(
            page, "task-3", {"full_name": "Eddie Murphy Jr."}, artifact_prefix="intake"
        )

    assert "full_name" in str(exc_info.value)
    # The abort reports what's actually on the page — real data for the
    # next fix, not another blind selector guess.
    assert "case_notes" in str(exc_info.value)
    assert exc_info.value.error_text is not None and "case_notes" in exc_info.value.error_text


def test_aborts_when_only_first_name_field_exists_not_last():
    page = _FakePage({_FIRST_NAME_SELECTOR: "x"})

    with pytest.raises(RetryableExecutionError, match="full_name"):
        _fill_identity_fields_or_abort(
            page, "task-4", {"full_name": "Eddie Murphy Jr."}, artifact_prefix="intake"
        )


def test_single_word_name_does_not_attempt_split_fallback():
    """A single-word value has no space to split on — the fallback is
    skipped entirely and the field aborts if no combined selector matched,
    rather than filling only a first name and leaving last name blank."""
    page = _FakePage({_FIRST_NAME_SELECTOR: "x", _LAST_NAME_SELECTOR: "x"})

    with pytest.raises(RetryableExecutionError, match="full_name"):
        _fill_identity_fields_or_abort(
            page, "task-5", {"full_name": "Cher"}, artifact_prefix="intake"
        )


def test_describe_visible_form_fields_returns_page_evaluate_result():
    page = _FakePage({})

    description = _describe_visible_form_fields(page)

    assert "case_notes" in description


def test_describe_visible_form_fields_never_raises_if_evaluate_fails():
    class _BrokenPage:
        def evaluate(self, _js: str):
            raise RuntimeError("page crashed")

    description = _describe_visible_form_fields(_BrokenPage())

    assert "no visible form fields found" in description


def test_describe_visible_form_fields_reports_page_context_when_nothing_found():
    """Live tonight: even with the frame scan and a longer wait, nothing
    was found in any frame — meaning the page itself may never have
    rendered normally for an automated browser (bot-blocked, redirected,
    a JS-disabled shell). When every frame comes back empty, report page
    title/URL/frame count and a short snippet of whatever public copy IS
    on the page, so the next diagnosis has real signal instead of a bare
    "nothing found"."""

    class _EmptyFrame:
        url = "https://potterhandy.com/google-privacy-violations-lawsuit/"

        def evaluate(self, js: str):
            if "innerText" in js:
                return "Just a moment... Please enable JavaScript and cookies to continue"
            return []

    class _EmptyPage:
        frames = [_EmptyFrame()]
        url = "https://potterhandy.com/google-privacy-violations-lawsuit/"

        def title(self):
            return "Just a moment..."

        def evaluate(self, js: str):
            return self.frames[0].evaluate(js)

    description = _describe_visible_form_fields(_EmptyPage())

    assert "no visible form fields found" in description
    assert "title='Just a moment...'" in description
    assert "Please enable JavaScript" in description
    assert "potterhandy.com" in description


def test_describe_visible_form_fields_reports_fields_found_in_an_iframe():
    """A lead-capture form embedded via an external widget (HubSpot,
    Gravity Forms iframe embeds, etc.) lives in a child frame — Playwright
    drives the browser via CDP, so it can read that frame's DOM directly
    even across origins, unlike a plain document.querySelectorAll on the
    top-level page."""

    class _MainFrame:
        url = "https://potterhandy.com/google-privacy-violations-lawsuit/"

        def evaluate(self, _js: str):
            return []  # nothing on the top-level page itself

    class _EmbeddedFormFrame:
        url = "https://forms.hubspot.com/embed/12345"

        def evaluate(self, _js: str):
            return ["input[type=text](name=firstname,id=firstname-abc)"]

    class _PageWithIframe:
        frames = [_MainFrame(), _EmbeddedFormFrame()]

    description = _describe_visible_form_fields(_PageWithIframe())

    assert "forms.hubspot.com" in description
    assert "firstname" in description
