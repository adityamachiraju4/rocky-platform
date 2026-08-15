"""Pydantic v2 schemas for the Tasks capability.

Note the absence of ``project_id`` and ``user_id`` on write schemas: the
project comes from the path and is verified against the authenticated user;
ownership is never client-supplied.

``status`` is constrained to the two known values because completion drives a
side effect (``completed_at``). An unrecognized status would desync the two
fields, so it is rejected at the schema boundary with ``422``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["active", "complete"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None)
    status: TaskStatus = Field(default="active")


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: TaskStatus | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    status: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
