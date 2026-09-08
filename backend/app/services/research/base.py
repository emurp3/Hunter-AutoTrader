"""
Shared types for Hunter's research providers. A provider is a plain
callable `(httpx.Client) -> ResearchOutcome` that fetches from real,
named, authoritative sources and reports what it actually found — never
what Claude found manually and typed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.models.hunter_ledger import Disposition

DEFAULT_TIMEOUT = 15.0
DEFAULT_USER_AGENT = "HunterResearchEngine/0.1 (+https://hunter.onrender.com; opportunity verification)"


def build_client() -> httpx.Client:
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        follow_redirects=True,
    )


@dataclass
class EvidenceFinding:
    """One fetch attempt against one named authoritative source."""

    ok: bool
    source_url: str
    summary: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    http_status: Optional[int] = None
    error: Optional[str] = None
    network_reachable: bool = True  # False only for a transport-level failure


@dataclass
class ResearchOutcome:
    """What a provider concluded for one CanonicalOpportunity, built
    entirely from its own findings."""

    passed: bool
    findings: list[EvidenceFinding]
    eligibility: Optional[str] = None
    jurisdiction: Optional[str] = None
    current_lawful_implementation: Optional[str] = None
    execution_route: Optional[str] = None
    commander_checkpoint: Optional[str] = None
    disposition_override: Optional[Disposition] = None
    rescue_type: Optional[str] = None
    rescue_description: Optional[str] = None
    rescue_result: str = "pending"

    @property
    def network_reachable(self) -> bool:
        """False only when EVERY finding failed at the transport layer
        (DNS/connect/proxy-policy failure) — distinguishes 'couldn't
        reach the internet from here' from 'reached it and it says no'."""
        if not self.findings:
            return True
        return any(f.network_reachable for f in self.findings)

    def evidence_reference(self) -> str:
        parts = []
        for f in self.findings:
            tag = "ok" if f.ok else "fail"
            status = f" status={f.http_status}" if f.http_status is not None else ""
            err = f" error={f.error}" if f.error else ""
            parts.append(f"[{tag}{status}{err}] {f.source_url} — {f.summary}")
        return " | ".join(parts) if parts else "no findings recorded"
