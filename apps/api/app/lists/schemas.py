"""Pydantic schemas for Lists and ordered items."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ListCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)


class ListUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["archived"] | None = None

    @model_validator(mode="after")
    def reject_null_title(self) -> "ListUpdate":
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        return self


class ListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ListItemCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ListItemUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["complete"] | None = None

    @model_validator(mode="after")
    def reject_null_content(self) -> "ListItemUpdate":
        if "content" in self.model_fields_set and self.content is None:
            raise ValueError("content cannot be null")
        return self


class ListItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    list_id: uuid.UUID
    content: str
    status: str
    position: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
