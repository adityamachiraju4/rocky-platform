"""Pydantic schemas for Reminders."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.time import ensure_utc, resolve_timezone


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    due_at: datetime
    timezone: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return resolve_timezone(value).key if value is not None else None


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    notes: str | None
    due_at: datetime
    timezone: str
    status: str
    triggered_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
