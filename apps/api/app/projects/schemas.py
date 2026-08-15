"""Pydantic v2 schemas for the Projects capability.

Note the absence of ``user_id`` on write schemas: ownership is assigned by
the service from the authenticated user and is never client-supplied.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None)
    status: str = Field(default="active", max_length=32)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, max_length=32)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime
