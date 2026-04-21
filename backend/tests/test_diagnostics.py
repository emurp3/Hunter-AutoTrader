from __future__ import annotations

import uuid

from app.services import diagnostics as diag_svc


def test_diagnostics_metadata_is_json_safe_for_uuid() -> None:
    payload = diag_svc.record_error(
        "diag.test",
        "boom",
        metadata={
            "packet_uuid": uuid.uuid4(),
            "nested": {"attempt_uuid": uuid.uuid4()},
        },
    )

    assert isinstance(payload["metadata"]["packet_uuid"], str)
    assert isinstance(payload["metadata"]["nested"]["attempt_uuid"], str)
