"""Typed inputs for durable scheduling."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ScheduledJobCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    run_at: datetime
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    max_attempts: int = Field(default=3, ge=1, le=20)
