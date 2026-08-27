"""Wire schemas for Rocky transcription."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TranscriptionResponse(BaseModel):
    text: str = Field(min_length=1)
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
