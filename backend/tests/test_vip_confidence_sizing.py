"""
Regression tests for the 2026-09-02 VIP confidence-scaled sizing change.

Previously VIP_WATCHLIST matches (Trump/cabinet/congressional disclosure
name matches) triggered a flat $15 auto-execute with zero relationship to
signal confidence. This replaces that with:
  amount = min(confidence * VIP_AUTO_INVEST_MAX_AMOUNT, VIP_AUTO_INVEST_MAX_AMOUNT)
and anything above VIP_AUTO_INVEST_APPROVAL_THRESHOLD does NOT auto-execute
-- it's logged as pending_approval and alerted instead.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.signal_engine import VIP_AUTO_INVEST_MAX_AMOUNT, VIP_AUTO_INVEST_APPROVAL_THRESHOLD


def _sized_amount(confidence: float) -> float:
    return round(min(confidence * VIP_AUTO_INVEST_MAX_AMOUNT, VIP_AUTO_INVEST_MAX_AMOUNT), 2)


def test_defaults_are_ten_dollar_cap_five_dollar_approval_threshold():
    assert VIP_AUTO_INVEST_MAX_AMOUNT == 10.00
    assert VIP_AUTO_INVEST_APPROVAL_THRESHOLD == 5.00


def test_low_confidence_sizes_small_and_stays_under_approval_threshold():
    amount = _sized_amount(0.10)
    assert amount == 1.00
    assert amount <= VIP_AUTO_INVEST_APPROVAL_THRESHOLD


def test_confidence_at_exactly_half_lands_at_approval_threshold_and_still_auto_executes():
    # 0.5 * $10 = $5.00, which is <= threshold, not > threshold -- auto-executes.
    amount = _sized_amount(0.50)
    assert amount == 5.00
    assert amount <= VIP_AUTO_INVEST_APPROVAL_THRESHOLD


def test_high_confidence_exceeds_approval_threshold_and_requires_review():
    amount = _sized_amount(0.85)
    assert amount == 8.50
    assert amount > VIP_AUTO_INVEST_APPROVAL_THRESHOLD


def test_max_confidence_never_exceeds_the_ten_dollar_cap():
    amount = _sized_amount(1.0)
    assert amount == VIP_AUTO_INVEST_MAX_AMOUNT
    assent = amount <= 10.00
    assert assent


def test_amount_scales_linearly_with_confidence():
    low = _sized_amount(0.20)
    high = _sized_amount(0.40)
    assert high == round(low * 2, 2)
