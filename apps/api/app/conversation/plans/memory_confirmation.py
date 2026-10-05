"""Internal single-forget confirmation envelope; never a provider plan schema."""
from typing import Literal

from pydantic import Field, model_validator

from app.conversation.plans.base import ProposedPlan, ProposedPlanStep


class MemoryForgetPlan(ProposedPlan):
    confirmation_action: Literal["memory.forget"] = "memory.forget"
    steps: list[ProposedPlanStep] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def only_forget(self) -> "MemoryForgetPlan":
        step = self.steps[0]
        if (step.action != "memory.forget" or step.id != "step_1"
                or step.result_of is not None or step.depends_on):
            raise ValueError("single confirmation accepts only a grounded memory forget")
        return self
