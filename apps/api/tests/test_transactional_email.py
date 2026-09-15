from __future__ import annotations

import pytest

from app.transactional_email.provider import TransactionalEmail
from app.transactional_email.resend import ResendTransactionalEmailProvider


class FakeResponse:
    def raise_for_status(self) -> None:
        pass


class FakeAsyncClient:
    def __init__(self) -> None:
        self.request: dict | None = None

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.request = {"url": url, **kwargs}
        return FakeResponse()


@pytest.mark.asyncio
async def test_resend_sends_reply_to(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient()
    monkeypatch.setattr(
        "app.transactional_email.resend.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    provider = ResendTransactionalEmailProvider(
        api_key="test-key",
        sender="Rocky OS <noreply@rockyos.in>",
    )

    await provider.send(
        TransactionalEmail(
            recipient="user@example.com",
            subject="Test",
            html="<p>Test</p>",
            reply_to="support@rockyos.in",
        )
    )

    assert client.request is not None
    assert client.request["url"] == "https://api.resend.com/emails"
    assert client.request["json"] == {
        "from": "Rocky OS <noreply@rockyos.in>",
        "to": ["user@example.com"],
        "subject": "Test",
        "html": "<p>Test</p>",
        "reply_to": "support@rockyos.in",
    }
