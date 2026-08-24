"""Session domain events (ADR-0009: Capability Cooperation).

In-process, synchronous, infrastructure-free. Events carry ONLY domain
information — no ORM objects, no HTTP objects, no persistence concerns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """Immutable base for all domain events."""
    session_id: int
    user_id: int
    occurred_at: datetime = field(default_factory=_now)
    reason: str | None = None


@dataclass(frozen=True)
class SessionCreated(DomainEvent):
    pass


@dataclass(frozen=True)
class SessionRevoked(DomainEvent):
    pass


@dataclass(frozen=True)
class SessionExpired(DomainEvent):
    pass


@dataclass(frozen=True)
class SessionExtended(DomainEvent):
    pass
