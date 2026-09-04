"""Email verification and password recovery lifecycle."""
from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.action_repository import AuthActionTokenRepository
from app.core.security import hash_password
from app.core.settings import (
    get_auth_action_token_pepper,
    get_email_verification_cooldown,
    get_email_verification_ttl,
    get_password_reset_ttl,
    get_public_app_url,
)
from app.models.auth_action_token import AuthActionToken
from app.models.session import Session
from app.models.user import User
from app.transactional_email.provider import (
    EmailDeliveryError,
    TransactionalEmail,
    TransactionalEmailProvider,
)

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"
_TOKEN_BYTES = 32


class InvalidAuthActionTokenError(ValueError):
    """Raised for missing, expired, consumed, or wrong-purpose action tokens."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _hash_action_token(token: str) -> str:
    return hmac.new(
        get_auth_action_token_pepper(),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class AuthActionService:
    def __init__(self, session: AsyncSession, email_provider: TransactionalEmailProvider) -> None:
        self._db = session
        self._tokens = AuthActionTokenRepository(session)
        self._email = email_provider

    async def _find_user(self, email: str) -> User | None:
        result = await self._db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def _lock_user(self, user_id: uuid.UUID) -> User | None:
        result = await self._db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _create_token(self, user: User, purpose: str) -> tuple[str, AuthActionToken]:
        now = _now()
        await self._tokens.consume_active(user.id, purpose, now)
        plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
        lifetime = get_email_verification_ttl() if purpose == VERIFY_EMAIL else get_password_reset_ttl()
        token = AuthActionToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_hash_action_token(plaintext),
            expires_at=now + lifetime,
            created_at=now,
        )
        self._db.add(token)
        await self._db.commit()
        return plaintext, token

    async def _deliver(self, user: User, purpose: str, plaintext: str, token: AuthActionToken) -> None:
        query = urlencode({"token": plaintext})
        if purpose == VERIFY_EMAIL:
            url = f"{get_public_app_url()}/verify-email?{query}"
            subject = "Verify your Rocky OS email"
            heading = "Verify your email"
            hours = int(get_email_verification_ttl().total_seconds() / 3600)
            copy = f"Use this link within {hours} hours to finish setting up your Rocky workspace."
        else:
            url = f"{get_public_app_url()}/reset-password?{query}"
            subject = "Reset your Rocky OS password"
            heading = "Reset your password"
            minutes = int(get_password_reset_ttl().total_seconds() / 60)
            copy = f"Use this link within {minutes} minutes to choose a new password. If you did not request this, you can ignore this email."
        name = html.escape(user.full_name or "there")
        safe_url = html.escape(url, quote=True)
        message = TransactionalEmail(
            recipient=user.email,
            subject=subject,
            html=(
                f"<h1>{heading}</h1><p>Hi {name},</p><p>{copy}</p>"
                f'<p><a href="{safe_url}">Continue to Rocky OS</a></p>'
            ),
        )
        try:
            await self._email.send(message)
        except EmailDeliveryError:
            token.consumed_at = _now()
            await self._db.commit()
            raise

    async def send_verification_for_user(self, user: User, *, enforce_cooldown: bool = True) -> bool:
        if user.is_verified or not user.is_active:
            return False
        if enforce_cooldown:
            locked = await self._lock_user(user.id)
            if locked is None or locked.is_verified or not locked.is_active:
                return False
            user = locked
            latest = await self._tokens.latest_active(user.id, VERIFY_EMAIL)
            if latest is not None and _aware(latest.created_at) + get_email_verification_cooldown() > _now():
                return False
        plaintext, token = await self._create_token(user, VERIFY_EMAIL)
        await self._deliver(user, VERIFY_EMAIL, plaintext, token)
        return True

    async def request_email_verification(self, email: str) -> bool:
        user = await self._find_user(email)
        if user is None:
            return False
        return await self.send_verification_for_user(user)

    async def request_password_reset(self, email: str) -> bool:
        user = await self._find_user(email)
        if user is None or not user.is_active:
            return False
        user = await self._lock_user(user.id)
        if user is None or not user.is_active:
            return False
        plaintext, token = await self._create_token(user, RESET_PASSWORD)
        await self._deliver(user, RESET_PASSWORD, plaintext, token)
        return True

    async def _valid_token(self, plaintext: str, purpose: str) -> AuthActionToken:
        token_hash = _hash_action_token(plaintext)
        token = await self._tokens.get_for_update(token_hash, purpose)
        if (
            token is None
            or not hmac.compare_digest(token.token_hash, token_hash)
            or token.consumed_at is not None
            or _aware(token.expires_at) <= _now()
        ):
            raise InvalidAuthActionTokenError("Token is invalid or expired.")
        return token

    async def confirm_email_verification(self, plaintext: str) -> User:
        token = await self._valid_token(plaintext, VERIFY_EMAIL)
        user = await self._db.get(User, token.user_id)
        if user is None or not user.is_active:
            raise InvalidAuthActionTokenError("Token is invalid or expired.")
        token.consumed_at = _now()
        user.is_verified = True
        await self._db.commit()
        await self._db.refresh(user)
        return user

    async def confirm_password_reset(self, plaintext: str, new_password: str) -> None:
        token = await self._valid_token(plaintext, RESET_PASSWORD)
        user = await self._db.get(User, token.user_id)
        if user is None or not user.is_active:
            raise InvalidAuthActionTokenError("Token is invalid or expired.")
        now = _now()
        user.password_hash = hash_password(new_password)
        user.session_version += 1
        await self._tokens.consume_active(user.id, RESET_PASSWORD, now)
        await self._db.execute(
            update(Session)
            .where(Session.user_id == user.id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self._db.commit()
