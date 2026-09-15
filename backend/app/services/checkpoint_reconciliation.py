"""Generic reconciliation of persisted human-input checkpoints.

Checkpoint text is a durable snapshot, not authoritative truth.  Before a
checkpoint is shown or resumed, requirements are re-evaluated against answers,
durable objective memory, the Commander profile, and mission-acquired facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlmodel import Session

from app.services import commander_identity, memory


@dataclass(frozen=True)
class Requirement:
    label: str
    alternatives: tuple[str, ...] = ()


def extract_requirements(checkpoint: str) -> list[Requirement]:
    text = checkpoint or ""
    found: list[Requirement] = []
    if re.search(r"full legal name|full name|legal name", text, re.I):
        found.append(Requirement("full legal name"))
    if re.search(r"email\s*(?:or|/|and)\s*phone|phone\s*(?:or|/|and)\s*email|contact", text, re.I):
        found.append(Requirement("contact information", ("email", "phone")))
    elif re.search(r"email", text, re.I):
        found.append(Requirement("email", ("email",)))
    elif re.search(r"phone", text, re.I):
        found.append(Requirement("phone", ("phone",)))
    if re.search(r"date range|dates? of use|approximate date", text, re.I):
        found.append(Requirement("Incognito date range"))
    return found


def reconcile_checkpoint(session: Session, checkpoint: str, *, objective_id: str | None,
                         existing_answer: str | None = None,
                         acquired: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Return unresolved checkpoint text and the values that satisfied fields."""
    facts = memory.fact_context(memory.active_facts(session, objective_id))
    profile = {k: commander_identity.get_identity_field(k) for k in commander_identity.FIELD_ENV_VARS}
    profile = {k: v for k, v in profile.items() if v}
    sources = {**facts, **(acquired or {}), **profile}
    answer = (existing_answer or "").strip()
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    requirements = extract_requirements(checkpoint)
    if not requirements:
        return checkpoint, resolved
    aliases = {
        "full legal name": "full_name",
        "Incognito date range": "date_range",
    }
    for req in requirements:
        if req.alternatives:
            value = next((sources.get(k) for k in req.alternatives if sources.get(k)), None)
        else:
            value = sources.get(req.label) or sources.get(req.label.replace(" ", "_"))
            value = value or sources.get(aliases.get(req.label, ""))
        if (
            not value
            and answer
            and req.label == "Incognito date range"
            and re.search(r"\b(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:19|20)\d{2}\b", answer)
        ):
            value = answer
        if not value and answer and re.search(re.escape(req.label.split()[0]), answer, re.I):
            value = answer
        if value:
            resolved[req.label] = value
        else:
            unresolved.append(req.label)
    if not unresolved:
        return "", resolved
    return "Hunter still needs: " + ", ".join(unresolved) + ".", resolved
