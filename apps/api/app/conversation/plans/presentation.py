"""Concise user-facing plan summaries without internal identifiers."""
from __future__ import annotations

from app.conversation.plans.base import ExecutablePlan, PlanResult, PlanStatus, ProposedPlan


def confirmation_reply(plan: ExecutablePlan) -> str:
    lines = ["I can do that. The plan is:"]
    for index, step in enumerate(plan.steps, 1):
        label = step.purpose or step.definition.description.rstrip(".")
        lines.append(f"{index}. {label}")
    lines.append("One or more steps change existing data. Shall I proceed?")
    return "\n".join(lines)


def pending_plan_summary(plan: ProposedPlan) -> str:
    """Minimal human-readable context for confirmation classification."""

    lines = []
    for index, step in enumerate(plan.steps, 1):
        lines.append(f"{index}. {step.purpose or step.action}")
    return "\n".join(lines)


def result_reply(result: PlanResult) -> str:
    completed = [
        step.result.display_summary
        for step in result.steps
        if step.status == "completed" and step.result is not None
    ]
    if result.status is PlanStatus.SUCCESS:
        return "Done. " + " ".join(completed)
    if result.status is PlanStatus.PARTIAL:
        prefix = " ".join(completed)
        if any(
            step.failure == "result_persistence_failed"
            for step in result.steps
        ):
            return (
                f"{prefix} That action succeeded, but I couldn't save its "
                "conversation reference, so I stopped before running anything else."
            )
        return f"{prefix} I couldn't complete the next step, so I stopped the plan."
    return "I couldn't complete the plan, so nothing after the failed step was run."


def durable_result_payload(result: PlanResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "failed_step": result.failed_step,
        "steps": [
            {"id": step.step_id, "action": step.action, "status": step.status, "failure": step.failure}
            for step in result.steps
        ],
    }
