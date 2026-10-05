"""Strict semantic inputs for explicit personal memory."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MEMORY_SUBJECT_MAX = 255
MEMORY_CONTENT_MAX = 2000
MemoryKind = Literal["fact", "preference"]
MemorySubject = Annotated[str, Field(min_length=1, max_length=MEMORY_SUBJECT_MAX)]
MemoryContent = Annotated[str, Field(min_length=1, max_length=MEMORY_CONTENT_MAX)]


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    kind: MemoryKind
    subject: MemorySubject
    content: MemoryContent


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    kind: MemoryKind | None = None
    subject: MemorySubject | None = None
    content: MemoryContent | None = None
    status: Literal["forgotten"] | None = None

    @model_validator(mode="after")
    def require_update(self) -> MemoryUpdate:
        if not self.model_fields_set:
            raise ValueError("memory update requires a change")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("memory update fields cannot be null")
        return self


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    kind: MemoryKind
    subject: str
    content: str
    status: Literal["active", "forgotten"]
    source: Literal["explicit_user"]
    source_thread_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    forgotten_at: datetime | None
