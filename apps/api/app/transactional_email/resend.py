"""Resend implementation of Rocky's transactional email contract."""
from __future__ import annotations

import httpx

from app.transactional_email.provider import (
    EmailDeliveryError,
    TransactionalEmail,
)


class ResendTransactionalEmailProvider:
    def __init__(self, api_key: str, sender: str, timeout_seconds: float = 10.0) -> None:
        self._api_key = api_key
        self._sender = sender
        self._timeout_seconds = timeout_seconds

    async def send(self, message: TransactionalEmail) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "from": self._sender,
                        "to": [message.recipient],
                        "subject": message.subject,
                        "html": message.html,
                    },
                )
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmailDeliveryError("Transactional email delivery failed.") from exc
