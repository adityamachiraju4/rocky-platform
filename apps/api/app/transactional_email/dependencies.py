"""Dependency wiring for the configured transactional email provider."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.settings import get_email_from, get_resend_api_key
from app.transactional_email.provider import TransactionalEmailProvider
from app.transactional_email.resend import ResendTransactionalEmailProvider


def get_transactional_email_provider() -> TransactionalEmailProvider:
    return ResendTransactionalEmailProvider(
        api_key=get_resend_api_key(),
        sender=get_email_from(),
    )


TransactionalEmailProviderDep = Annotated[
    TransactionalEmailProvider, Depends(get_transactional_email_provider)
]
