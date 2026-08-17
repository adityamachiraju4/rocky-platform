"""Pydantic schemas for the Speech rendering capability."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

MAX_SPEECH_TEXT_CHARS = 1200


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Speech text must not be empty.")
        return stripped
