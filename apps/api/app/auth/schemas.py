"""Pydantic v2 schemas for the Authentication capability."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials plus optional device continuity information.

    ``client_id`` is a client-generated opaque stable identifier. When
    supplied, the device is resolved by ``(user_id, client_id)`` and reused
    across logins; when absent, a fresh device row is created (no server-side
    identifier is manufactured).
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)
    client_id: uuid.UUID | None = None
    device_name: str | None = Field(default=None, max_length=255)
    device_type: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=64)


class RefreshRequest(BaseModel):
    """Carries the opaque refresh token to be rotated."""

    refresh_token: str = Field(min_length=1, max_length=4096)


class LogoutRequest(BaseModel):
    """Carries the opaque refresh token whose session is to be revoked."""

    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenResponse(BaseModel):
    """Access/refresh pair returned by login and refresh.

    ``access_token`` is a short-lived JWT (carries ``sid``). ``refresh_token``
    is the plaintext opaque secret, shown exactly once; only its peppered hash
    is persisted.
    """

    model_config = ConfigDict(from_attributes=True)

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


__all__ = [
    "LoginRequest",
    "RefreshRequest",
    "LogoutRequest",
    "TokenResponse",
]
