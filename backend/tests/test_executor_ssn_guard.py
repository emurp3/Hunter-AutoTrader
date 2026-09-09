"""
Commander's rule (2026-09-09): name, DOB, address, and email may be used
by Hunter's executors autonomously; SSN requires Commander's explicit,
per-submission consent every time. This is enforced as a hard,
code-level check in the executor itself (_assert_ssn_use_explicitly_approved)
so a future dispatch mistake can't silently let an SSN through — not a
convention the dispatcher is trusted to honor on its own.
"""

from __future__ import annotations

import pytest

from app.worker.executors import WorkerExecutionError, _assert_ssn_use_explicitly_approved


def test_ssn_present_without_approval_flag_refuses():
    with pytest.raises(WorkerExecutionError) as exc_info:
        _assert_ssn_use_explicitly_approved({}, {"ssn": "123-45-6789", "full_name": "Eddie Murphy Jr."})
    assert exc_info.value.escalation_type == "commander_boundary"


def test_ssn_present_with_approval_flag_passes():
    _assert_ssn_use_explicitly_approved(
        {"ssn_explicitly_approved": True}, {"ssn": "123-45-6789"}
    )  # must not raise


def test_no_ssn_present_never_requires_approval_flag():
    _assert_ssn_use_explicitly_approved(
        {}, {"full_name": "Eddie Murphy Jr.", "email": "eddie@example.com", "dob": "1990-01-01"}
    )  # must not raise — none of these need explicit consent


def test_approval_flag_false_still_refuses():
    with pytest.raises(WorkerExecutionError):
        _assert_ssn_use_explicitly_approved({"ssn_explicitly_approved": False}, {"ssn": "123-45-6789"})


def test_empty_identity_fields_never_requires_approval_flag():
    _assert_ssn_use_explicitly_approved({}, {})  # must not raise
