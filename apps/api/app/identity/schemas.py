"""Pydantic v2 schemas for the Identity subsystem."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """Payload for creating a user. Plaintext password is hashed by the
    service and never persisted or echoed back."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
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
