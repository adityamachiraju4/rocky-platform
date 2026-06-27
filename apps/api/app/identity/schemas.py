"""Pydantic v2 schemas for the Identity subsystem.

These mirror the ORM models in ``models.py`` and are used for request
validation and response serialization. All read schemas enable
``from_attributes`` so they can be built directly from ORM instances.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --------------------------------------------------------------------------- #
# User
# --------------------------------------------------------------------------- #
class UserBase(BaseModel):
    """Shared user fields."""

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    timezone: str = Field(default="UTC", max_length=64)
    locale: str = Field(default="en-US", max_length=16)


class UserCreate(UserBase):
    """Payload for creating a user."""


class UserUpdate(BaseModel):
    """Partial-update payload for a user. All fields optional."""

    full_name: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)
    is_active: bool | None = None


class UserRead(UserBase):
    """User representation returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
class PreferencesUpdate(BaseModel):
    """Partial-update payload for preferences. All fields optional."""

    theme: str | None = Field(default=None, max_length=32)
    language: str | None = Field(default=None, max_length=16)
    notifications: dict | None = None


class PreferencesRead(BaseModel):
    """Preferences representation returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    theme: str
    language: str
    notifications: dict
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Device
# --------------------------------------------------------------------------- #
class DeviceRead(BaseModel):
    """Device representation returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    device_id: str
    platform: str
    device_name: str | None
    last_seen: datetime
    created_at: datetime
