"""Sequential stop-on-first-failure execution through the CI-3 runtime."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.conversation.actions.base import ActionResult, GroundedResultReference
from app.conversation.plans.base import (
    ExecutablePlan,
    ExecutablePlanStep,
    PlanResult,
    PlanStatus,
    PlanStepResult,
)

logger = logging.getLogger(__name__)

PrepareStep = Callable[
    [ExecutablePlanStep, dict[str, tuple[GroundedResultReference, ...]]],
    Awaitable[tuple[Any, Any]],
]
ExecuteStep = Callable[[Any, Any], Awaitable[ActionResult]]
RememberResult = Callable[[ActionResult], Awaitable[None]]


class PlanExecutor:
    async def execute(
        self,
        plan: ExecutablePlan,
        *,
        prepare_step: PrepareStep,
        execute_step: ExecuteStep,
        remember_result: RememberResult,
    ) -> PlanResult:
        step_results: list[PlanStepResult] = []
        references: dict[str, tuple[GroundedResultReference, ...]] = {}
        completed: set[str] = set()

        logger.info("Plan execution started", extra={"plan_id": str(plan.id), "step_count": len(plan.steps)})
        for index, step in enumerate(plan.steps):
            if any(dependency not in completed for dependency in step.depends_on):
                step_results.append(PlanStepResult(step.id, step.definition.name, "skipped", failure="dependency_failed"))
                return self._failed_result(plan, step_results, references, step.id, index)
            try:
                action, world = await prepare_step(step, references)
                result = await execute_step(action, world)
            except Exception as exc:  # noqa: BLE001 - a plan must stop on any step failure
                logger.warning(
                    "Plan step failed",
                    extra={"plan_id": str(plan.id), "step_id": step.id, "action": step.definition.name},
                    exc_info=True,
                )
                step_results.append(
                    PlanStepResult(step.id, step.definition.name, "failed", failure=type(exc).__name__)
                )
                return self._failed_result(plan, step_results, references, step.id, index)

            if not result.success:
                logger.info(
                    "Plan step returned failure",
                    extra={"plan_id": str(plan.id), "step_id": step.id, "action": step.definition.name},
                )
                step_results.append(
                    PlanStepResult(
                        step.id, step.definition.name, "failed", result=result,
                        failure=result.failure_type or "action_failed",
                    )
                )
                return self._failed_result(plan, step_results, references, step.id, index)

            completed.add(step.id)
            references[step.id] = result.references
            try:
                await remember_result(result)
            except Exception as exc:  # noqa: BLE001 - mutation already succeeded
                # The action is never retried. Record its real success, stop
                # the plan, and let the durable lifecycle become failed.
                logger.warning(
                    "Plan result persistence failed after action success",
                    extra={
                        "plan_id": str(plan.id), "step_id": step.id,
                        "action": step.definition.name,
                    },
                    exc_info=True,
                )
                step_results.append(
                    PlanStepResult(
                        step.id, step.definition.name, "completed",
                        result=result, failure="result_persistence_failed",
                    )
                )
                return self._failed_result(
                    plan, step_results, references, step.id, index
                )
            step_results.append(
                PlanStepResult(
                    step.id, step.definition.name, "completed", result=result
                )
            )
            logger.info(
                "Plan step completed",
                extra={"plan_id": str(plan.id), "step_id": step.id, "action": step.definition.name},
            )

        logger.info("Plan completed", extra={"plan_id": str(plan.id), "step_count": len(plan.steps)})
        return PlanResult(plan.id, PlanStatus.SUCCESS, tuple(step_results), references=references)

    @staticmethod
    def _failed_result(
        plan: ExecutablePlan,
        results: list[PlanStepResult],
        references: dict[str, tuple[GroundedResultReference, ...]],
        failed_step: str,
        failed_index: int,
    ) -> PlanResult:
        for step in plan.steps[failed_index + 1:]:
            results.append(PlanStepResult(step.id, step.definition.name, "not_run"))
        status = PlanStatus.PARTIAL if any(item.status == "completed" for item in results) else PlanStatus.FAILED
        return PlanResult(plan.id, status, tuple(results), failed_step, references)
