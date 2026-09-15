"""Provider-neutral transactional email contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmailDeliveryError(RuntimeError):
    """Raised when a configured provider cannot accept a message."""


@dataclass(frozen=True, slots=True)
class TransactionalEmail:
    recipient: str
    subject: str
    html: str
    reply_to: str | None = None


class TransactionalEmailProvider(Protocol):
    async def send(self, message: TransactionalEmail) -> None: ...
