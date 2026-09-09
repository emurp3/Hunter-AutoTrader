"""
Commander's identity fields (DOB, SSN, address, etc.) for use when a
specific, Commander-approved application/filing step needs them.

Deliberately NOT stored in the database, NOT in any file, and NEVER
included in Hunter AI's chat context — these are read directly from
environment variables (Render's own encrypted-at-rest secrets store),
meant to be set directly by Commander through the Render dashboard,
never through this codebase, this chat, or git history. There is no
HTTP endpoint anywhere in this app that accepts or returns a raw value
for any of these fields, and there must not be one — that would put
Commander's SSN through this application's request path and logs for
no real benefit over setting it directly in Render.

Hunter AI's chat context only ever sees which fields are PRESENT
(booleans, via get_identity_field_presence()) — never the values.
Reading an actual value (get_identity_field()) is for a future,
specific, Commander-approved executor to do at the point of use, not
for the conversational assistant. No such executor exists yet — storage
only, per the standing addendum rule that any consequential external
submission stays a separate Commander-approved step.
"""

from __future__ import annotations

import os

FIELD_ENV_VARS: dict[str, str] = {
    "full_name": "HUNTER_COMMANDER_FULL_NAME",
    "dob": "HUNTER_COMMANDER_DOB",
    "ssn": "HUNTER_COMMANDER_SSN",
    "email": "HUNTER_COMMANDER_EMAIL",
    "phone": "HUNTER_COMMANDER_PHONE",
    "address_line1": "HUNTER_COMMANDER_ADDRESS_LINE1",
    "address_line2": "HUNTER_COMMANDER_ADDRESS_LINE2",
    "city": "HUNTER_COMMANDER_CITY",
    "state": "HUNTER_COMMANDER_STATE",
    "zip": "HUNTER_COMMANDER_ZIP",
}


def get_identity_field(field: str) -> str | None:
    """Read one identity field's real value. Call this only from a
    specific, Commander-approved executor at the point of use — never
    from the chat context builder, never logged, never returned by any
    API response."""
    env_var = FIELD_ENV_VARS.get(field)
    if not env_var:
        raise ValueError(f"Unknown identity field: {field}")
    return os.getenv(env_var) or None


def get_identity_field_presence() -> dict[str, bool]:
    """Which fields are set, never their values — safe to show in chat
    or return from an API response."""
    return {field: bool(os.getenv(env_var)) for field, env_var in FIELD_ENV_VARS.items()}
