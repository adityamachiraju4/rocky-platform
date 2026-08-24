"""Pydantic v2 schemas for the Identity subsystem."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.time import resolve_timezone


def _validated_timezone(value: str | None) -> str | None:
    return resolve_timezone(value).key if value is not None else None


class UserCreate(BaseModel):
    """Payload for creating a user. Plaintext password is hashed by the
    service and never persisted or echoed back."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)
    full_name: str | None = Field(default=None, max_length=255)
    timezone: str = Field(default="UTC", max_length=64)

    _validate_timezone = field_validator("timezone")(_validated_timezone)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)

    _validate_timezone = field_validator("timezone")(_validated_timezone)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    timezone: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class DeviceRead(BaseModel):
    """Mirrors canonical app/models/device.py exactly."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    device_name: str | None
    device_type: str | None
    platform: str | None
    last_seen: datetime | None
    created_at: datetime
