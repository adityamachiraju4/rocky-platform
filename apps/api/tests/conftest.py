from __future__ import annotations

import pytest

from app.main import app as fastapi_app
from app.transactional_email.dependencies import get_transactional_email_provider
from app.transactional_email.provider import TransactionalEmail
from app.transactional_email.provider import EmailDeliveryError


class FakeTransactionalEmailProvider:
    def __init__(self) -> None:
        self.messages: list[TransactionalEmail] = []
        self.fail = False

    async def send(self, message: TransactionalEmail) -> None:
        if self.fail:
            raise EmailDeliveryError("test delivery failure")
        self.messages.append(message)


@pytest.fixture(autouse=True)
def fake_transactional_email(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ACTION_TOKEN_PEPPER", "test-auth-action-pepper")
    monkeypatch.setenv("PUBLIC_APP_URL", "http://localhost:5173")
    provider = FakeTransactionalEmailProvider()
    fastapi_app.dependency_overrides[get_transactional_email_provider] = lambda: provider
    try:
        yield provider
    finally:
        fastapi_app.dependency_overrides.pop(get_transactional_email_provider, None)
