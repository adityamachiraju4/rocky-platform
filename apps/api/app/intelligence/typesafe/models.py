"""Validated TypeSafe API boundary models from the public OpenAPI schema."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonState = str | dict[str, Any] | list[Any]
JsonDescription = JsonState | None
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelMetadata(_StrictModel):
    name: str
    description: str
    release_date: str


class ModelsResponse(_StrictModel):
    models: list[ModelMetadata]


class ChoiceQuestion(_StrictModel):
    type: Literal["choice"] = "choice"
    criteria: dict[str, JsonDescription]
    instructions: JsonDescription = None


class NoulCriteria(_StrictModel):
    true: JsonDescription = None
    false: JsonDescription = None


class NoulQuestion(_StrictModel):
    type: Literal["noul"] = "noul"
    criteria: NoulCriteria | None = None
    instructions: JsonDescription = None


class ScoreQuestion(_StrictModel):
    type: Literal["score"] = "score"
    criteria: list[JsonState] = Field(min_length=1)
    instructions: JsonDescription = None


Question = Annotated[
    ChoiceQuestion | NoulQuestion | ScoreQuestion,
    Field(discriminator="type"),
]


class SystemOneRequest(_StrictModel):
    state: JsonState
    model: str
    questions: dict[str, Question] = Field(min_length=1)


class ChoiceAnswer(_StrictModel):
    type: Literal["choice"]
    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, Probability]


class NoulAnswer(_StrictModel):
    type: Literal["noul"]
    noul: float = Field(ge=0.0, le=1.0)


class ScoreAnswer(_StrictModel):
    type: Literal["score"]
    score: float
    confidence: float = Field(ge=0.0, le=1.0)
    legend: dict[str, JsonState]
    probabilities: dict[str, Probability]


Answer = Annotated[
    ChoiceAnswer | NoulAnswer | ScoreAnswer,
    Field(discriminator="type"),
]


class Usage(_StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class SystemOneResponse(_StrictModel):
    model: str
    answers: dict[str, Answer] = Field(min_length=1)
    usage: Usage


__all__ = [
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ModelMetadata",
    "ModelsResponse",
    "NoulAnswer",
    "NoulCriteria",
    "NoulQuestion",
    "SystemOneRequest",
    "SystemOneResponse",
]
