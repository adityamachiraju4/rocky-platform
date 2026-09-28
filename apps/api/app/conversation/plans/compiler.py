"""Fail-closed structural and registry validation for proposed plans."""
from __future__ import annotations

import uuid

from app.conversation.actions.base import ConfirmationPolicy, ReferencePolicy
from app.conversation.actions.registry import ACTION_REGISTRY, ActionRegistry
from app.conversation.plans.base import ExecutablePlan, ExecutablePlanStep, ProposedPlan


class PlanValidationError(ValueError):
    """A plan failed before any action was executed."""


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value.strip())
    except (ValueError, AttributeError):
        return False
    return True


class PlanCompiler:
    def __init__(self, action_registry: ActionRegistry = ACTION_REGISTRY) -> None:
        self._registry = action_registry

    def compile(
        self, proposed: ProposedPlan, *, plan_id: uuid.UUID | None = None
    ) -> ExecutablePlan:
        seen: dict[str, ExecutablePlanStep] = {}
        compiled: list[ExecutablePlanStep] = []
        requires_confirmation = False

        for position, step in enumerate(proposed.steps, 1):
            if step.id != f"step_{position}":
                raise PlanValidationError("step ids must match declared order")
            try:
                definition = self._registry.get(step.action)
                arguments = self._registry.parse_arguments(step.action, step.arguments)
            except Exception as exc:
                raise PlanValidationError(f"invalid plan step {step.id}") from exc

            if step.reference is not None and _looks_like_uuid(step.reference):
                raise PlanValidationError("raw identifiers are not valid references")
            if step.id in step.depends_on or step.result_of == step.id:
                raise PlanValidationError("a step cannot depend on itself")

            dependencies = tuple(dict.fromkeys((*step.depends_on, *(() if step.result_of is None else (step.result_of,)))))
            missing = [dependency for dependency in dependencies if dependency not in seen]
            if missing:
                raise PlanValidationError("dependencies must name earlier plan steps")

            has_reference = step.reference is not None or step.result_of is not None
            if definition.reference is ReferencePolicy.NONE and has_reference:
                raise PlanValidationError("action does not accept a reference")
            if definition.reference is ReferencePolicy.REQUIRED and not has_reference:
                raise PlanValidationError("action requires a reference")

            if step.result_of is not None:
                producer = seen[step.result_of].definition
                if (
                    producer.result_kind is None
                    or producer.result_kind != definition.reference_kind
                ):
                    raise PlanValidationError("incompatible step-result reference")

            compiled_step = ExecutablePlanStep(
                id=step.id,
                definition=definition,
                arguments=arguments,
                reference=step.reference,
                result_of=step.result_of,
                depends_on=dependencies,
                purpose=step.purpose,
            )
            compiled.append(compiled_step)
            seen[step.id] = compiled_step
            requires_confirmation |= (
                definition.confirmation is ConfirmationPolicy.PLAN_STEP
            )

        return ExecutablePlan(
            id=plan_id or uuid.uuid4(),
            proposed=proposed,
            steps=tuple(compiled),
            requires_confirmation=requires_confirmation,
        )
