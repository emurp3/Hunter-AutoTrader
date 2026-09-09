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

from app.worker.executors import RetryableExecutionError, _fill_identity_fields_or_abort

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

    with pytest.raises(RetryableExecutionError, match="full_name"):
        _fill_identity_fields_or_abort(
            page, "task-3", {"full_name": "Eddie Murphy Jr."}, artifact_prefix="intake"
        )


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
