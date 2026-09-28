"""Strict plan contracts. Plans are finite data, never autonomous loops."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation.actions.base import ActionDefinition, ActionResult, StrictActionArgs

MIN_PLAN_STEPS = 2
MAX_PLAN_STEPS = 8


class ProposedPlanStep(BaseModel):
    """One untrusted model-proposed action and its bounded references."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    id: str = Field(pattern=r"^step_[1-8]$")
    action: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reference: str | None = Field(default=None, min_length=1, max_length=500)
    result_of: str | None = Field(default=None, pattern=r"^step_[1-8]$")
    depends_on: list[str] = Field(default_factory=list, max_length=7)
    purpose: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_local_references(self) -> "ProposedPlanStep":
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("duplicate step dependency")
        for dependency in self.depends_on:
            if not re.fullmatch(r"step_[1-8]", dependency):
                raise ValueError("invalid step dependency")
        if self.reference is not None and self.result_of is not None:
            raise ValueError("a step may use one reference source")
        return self


class ProposedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    steps: list[ProposedPlanStep] = Field(
        min_length=MIN_PLAN_STEPS, max_length=MAX_PLAN_STEPS
    )
    summary: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_step_ids(self) -> "ProposedPlan":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate plan step id")
        return self


@dataclass(frozen=True)
class ExecutablePlanStep:
    id: str
    definition: ActionDefinition
    arguments: StrictActionArgs
    reference: str | None
    result_of: str | None
    depends_on: tuple[str, ...]
    purpose: str | None


@dataclass(frozen=True)
class ExecutablePlan:
    id: uuid.UUID
    proposed: ProposedPlan
    steps: tuple[ExecutablePlanStep, ...]
    requires_confirmation: bool


class PlanStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXPIRED = "expired"
    INVALID = "invalid"
    REJECTED = "rejected"
    ALREADY_HANDLED = "already_handled"


@dataclass(frozen=True)
class PlanStepResult:
    step_id: str
    action: str
    status: str
    result: ActionResult | None = None
    failure: str | None = None


@dataclass(frozen=True)
class PlanResult:
    plan_id: uuid.UUID
    status: PlanStatus
    steps: tuple[PlanStepResult, ...] = ()
    failed_step: str | None = None
    references: dict[str, tuple[Any, ...]] = field(default_factory=dict)

    @property
    def completed_steps(self) -> tuple[PlanStepResult, ...]:
        return tuple(step for step in self.steps if step.status == "completed")
