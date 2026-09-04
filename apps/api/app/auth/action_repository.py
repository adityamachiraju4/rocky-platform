"""Persistence operations for single-use authentication action tokens."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_action_token import AuthActionToken


class AuthActionTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, token_hash: str, purpose: str) -> AuthActionToken | None:
        result = await self._session.execute(
            select(AuthActionToken)
            .where(
                AuthActionToken.token_hash == token_hash,
                AuthActionToken.purpose == purpose,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def latest_active(self, user_id: uuid.UUID, purpose: str) -> AuthActionToken | None:
        result = await self._session.execute(
            select(AuthActionToken)
            .where(
                AuthActionToken.user_id == user_id,
                AuthActionToken.purpose == purpose,
                AuthActionToken.consumed_at.is_(None),
            )
            .order_by(AuthActionToken.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def consume_active(self, user_id: uuid.UUID, purpose: str, consumed_at: datetime) -> None:
        await self._session.execute(
            update(AuthActionToken)
            .where(
                AuthActionToken.user_id == user_id,
                AuthActionToken.purpose == purpose,
                AuthActionToken.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
        )
