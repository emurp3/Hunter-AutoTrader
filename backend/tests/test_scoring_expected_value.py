"""
Regression tests for the 2026-09-02 scoring rewrite (expected-value ranking).

Prior behavior (bug): score_opportunity() used raw estimated_profit against
a hard-clipped $10,000 log-scale ceiling (`min(profit_ratio, 1.0)`), so any
opportunity above $10k scored identically on the dominant profit axis
regardless of size, and confidence/probability was not multiplied into
profit at all -- a $500,000 opportunity at 5% odds and one at 95% odds
scored the same. This buried large, high-value opportunities under a pile
of small ones and ignored win probability entirely.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.income_source import IncomeSource, SourceStatus
from app.services.scoring import score_opportunity


def _make(profit, confidence, category="gig", origin="gig_scanner", desc="", next_action="", status=SourceStatus.new):
    return IncomeSource(
        source_id="test",
        description=desc,
        estimated_profit=profit,
        currency="USD",
        status=status,
        date_found=date.today(),
        next_action=next_action,
        notes="x",
        origin_module=origin,
        category=category,
        confidence=confidence,
    )


def test_large_ev_opportunity_is_not_capped_equal_to_10k_opportunity():
    huge = score_opportunity(_make(500_000, 0.5, category="grant", origin="rfp_scanner"))
    baseline_10k = score_opportunity(_make(10_000, 0.5))
    assert huge.score > baseline_10k.score, (
        "A $500k EV opportunity must score strictly higher than a $10k one -- "
        "the old ceiling clip made them equal."
    )


def test_confidence_is_multiplied_into_profit_not_just_a_flat_bonus():
    high_odds = score_opportunity(_make(500_000, 0.9, category="grant", origin="rfp_scanner"))
    low_odds = score_opportunity(_make(500_000, 0.05, category="grant", origin="rfp_scanner"))
    assert high_odds.score > low_odds.score, (
        "Same face-value opportunity at 90% odds must outscore the same "
        "opportunity at 5% odds -- probability must affect the profit axis, "
        "not just a small separate factor."
    )


def test_fast_identified_buyer_flip_beats_slower_similar_ev_opportunity():
    fast_flip = score_opportunity(
        _make(
            150, 0.85, category="marketplace", origin="marketplace_scanner",
            desc="Clearance TV 70% off", next_action="Same day pickup, identified buyer, ready to execute",
        )
    )
    slow_similar_ev = score_opportunity(
        _make(160, 0.8, category="grant", origin="rfp_scanner", desc="Small grant", next_action="Apply, multi-month review"),
    )
    assert fast_flip.score > slow_similar_ev.score, (
        "A short discovery-to-cash path should be able to edge out a "
        "similar-EV opportunity that takes months to resolve."
    )


def test_capability_fit_boosts_opportunities_matching_known_skills():
    with_fit = score_opportunity(
        _make(25, 0.5, category="digital", origin="digital_product_scanner",
              desc="Business dashboard template", next_action="Build + list on Gumroad/Etsy")
    )
    without_fit = score_opportunity(
        _make(25, 0.5, category="digital", origin="digital_product_scanner",
              desc="Generic small task", next_action="Do the thing")
    )
    assert with_fit.score > without_fit.score


def test_500k_grant_at_realistic_low_odds_still_outranks_small_high_confidence_gig():
    """The scenario that motivated this rewrite: a big, low-probability
    opportunity must not disappear beneath many small, safe ones."""
    grant = score_opportunity(
        _make(500_000, 0.08, category="grant", origin="rfp_scanner", desc="NSF cybersecurity grant")
    )
    small_safe_gig = score_opportunity(_make(40, 0.8, category="gig", origin="gig_scanner"))
    assert grant.score > small_safe_gig.score
    assert grant.priority_band in ("elite", "high")


def test_missing_confidence_does_not_zero_out_expected_value():
    no_confidence = _make(1000, None)
    no_confidence.confidence = None
    result = score_opportunity(no_confidence)
    zero_confidence = _make(1000, 0.0)
    zero_confidence_result = score_opportunity(zero_confidence)
    assert result.score > zero_confidence_result.score, (
        "Missing confidence should fall back to a reasonable default, not "
        "be treated the same as explicit 0% confidence."
    )
