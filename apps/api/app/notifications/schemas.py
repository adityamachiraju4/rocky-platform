"""Typed inputs and HTTP representations for Notifications."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NotificationCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=4000)
    source_type: str | None = Field(default=None, max_length=64)
    source_id: uuid.UUID | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationUpdate(BaseModel):
    status: Literal["read", "dismissed"]


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    title: str
    body: str
    status: str
    source_type: str | None
    source_id: uuid.UUID | None
    source_metadata: dict[str, Any]
    read_at: datetime | None
    dismissed_at: datetime | None
    created_at: datetime
