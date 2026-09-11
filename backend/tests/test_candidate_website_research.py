"""
Commander, 2026-09-11: "do not guess contacts" — _propose_candidate_business_website
must never be trusted on its own, and _business_name_appears_on_page is
the independent check that keeps a wrong/hallucinated candidate from
ever being treated as a real business's contact route.
"""

from __future__ import annotations

from app.worker import executors


def test_propose_candidate_returns_none_when_claude_is_not_confident(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "UNKNOWN")

    result = executors._propose_candidate_business_website("Example Dental", "dentist", "Macon, GA")

    assert result is None


def test_propose_candidate_returns_none_on_a_multi_word_non_url_reply(monkeypatch):
    """A defensive parse guard — Claude replying with prose instead of a
    bare domain must never be treated as a URL."""
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "I think it might be exampledental.com")

    result = executors._propose_candidate_business_website("Example Dental", "dentist", "Macon, GA")

    assert result is None


def test_propose_candidate_normalizes_a_bare_domain_to_https(monkeypatch):
    monkeypatch.setattr(executors, "_claude_text", lambda prompt: "exampledental.com")

    result = executors._propose_candidate_business_website("Example Dental", "dentist", "Macon, GA")

    assert result == "https://exampledental.com"


def test_propose_candidate_returns_none_when_claude_call_fails(monkeypatch):
    def _boom(prompt):
        raise RuntimeError("no API key")

    monkeypatch.setattr(executors, "_claude_text", _boom)

    result = executors._propose_candidate_business_website("Example Dental", "dentist", "Macon, GA")

    assert result is None


class _FakePage:
    def __init__(self, title: str, body: str) -> None:
        self._title = title
        self._body = body

    def title(self) -> str:
        return self._title

    def inner_text(self, selector: str) -> str:
        return self._body


def test_business_name_verification_matches_the_real_site():
    page = _FakePage("Example Dental — Family Dentistry in Macon", "Welcome to Example Dental...")

    assert executors._business_name_appears_on_page(page, "Example Dental LLC") is True


def test_business_name_verification_rejects_an_unrelated_site():
    page = _FakePage("Totally Different Business", "We sell widgets and gadgets.")

    assert executors._business_name_appears_on_page(page, "Example Dental LLC") is False


def test_business_name_verification_strips_common_legal_suffixes():
    page = _FakePage("Murphy — homepage", "")

    assert executors._business_name_appears_on_page(page, "Murphy LLC") is True


def test_business_name_verification_false_for_empty_or_generic_name():
    page = _FakePage("Anything", "Anything")

    assert executors._business_name_appears_on_page(page, "LLC Inc") is False
