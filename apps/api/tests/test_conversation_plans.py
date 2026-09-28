"""CI-4 bounded plan compilation and dependency safety contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.conversation.actions.base import ActionResult, GroundedResultReference
from app.conversation.plans.base import MAX_PLAN_STEPS, ProposedPlan, ProposedPlanStep
from app.conversation.plans.compiler import PlanCompiler, PlanValidationError
from app.conversation.plans.executor import PlanExecutor
from app.conversation.actions.registry import ACTION_REGISTRY
from app.conversation.understanding import UNDERSTANDING_JSON_SCHEMA


def step(
    number: int,
    action: str,
    arguments: dict[str, object] | None = None,
    *,
    reference: str | None = None,
    result_of: str | None = None,
    depends_on: list[str] | None = None,
) -> ProposedPlanStep:
    return ProposedPlanStep(
        id=f"step_{number}", action=action, arguments=arguments or {},
        reference=reference, result_of=result_of,
        depends_on=depends_on or [], purpose=f"Step {number}",
    )


def plan(*steps: ProposedPlanStep) -> ProposedPlan:
    return ProposedPlan(steps=list(steps), summary="A bounded plan")


def test_valid_two_step_plan_compiles() -> None:
    compiled = PlanCompiler().compile(plan(
        step(1, "project.create", {"name": "Japan Trip"}),
        step(2, "task.create", {"title": "Book Flights"},
             result_of="step_1", depends_on=["step_1"]),
    ))
    assert [item.definition.name for item in compiled.steps] == [
        "project.create", "task.create"
    ]
    assert compiled.requires_confirmation is False


def test_valid_maximum_size_plan_compiles() -> None:
    compiled = PlanCompiler().compile(plan(*[
        step(index, "project.create", {"name": f"Project {index}"})
        for index in range(1, MAX_PLAN_STEPS + 1)
    ]))
    assert len(compiled.steps) == MAX_PLAN_STEPS


@pytest.mark.parametrize("count", [0, 1, MAX_PLAN_STEPS + 1])
def test_invalid_plan_sizes_are_rejected(count: int) -> None:
    with pytest.raises(ValidationError):
        ProposedPlan(steps=[
            step(index, "project.create", {"name": str(index)})
            for index in range(1, count + 1)
        ])


def test_duplicate_step_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        plan(
            step(1, "project.create", {"name": "One"}),
            step(1, "project.create", {"name": "Two"}),
        )


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("system.delete_everything", {}),
        ("project.create", {}),
        ("project.create", {"name": "Trip", "owner_id": "foreign"}),
        ("project.create", {"name": 42}),
    ],
)
def test_unknown_or_malformed_actions_fail_closed(
    action: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(PlanValidationError):
        PlanCompiler().compile(plan(
            step(1, action, arguments),
            step(2, "project.create", {"name": "Safe"}),
        ))


def test_valid_result_dependency_has_compatible_kind() -> None:
    compiled = PlanCompiler().compile(plan(
        step(1, "list.create", {"title": "Shopping"}),
        step(2, "list.add_item", {"content": "Milk"},
             result_of="step_1", depends_on=["step_1"]),
    ))
    assert compiled.steps[1].depends_on == ("step_1",)


@pytest.mark.parametrize(
    "second",
    [
        step(2, "task.create", {"title": "Task"}, result_of="step_3"),
        step(2, "task.create", {"title": "Task"}, result_of="step_2"),
        step(2, "task.create", {"title": "Task"}, depends_on=["step_3"]),
    ],
)
def test_missing_forward_and_self_dependencies_are_rejected(
    second: ProposedPlanStep,
) -> None:
    with pytest.raises(PlanValidationError):
        PlanCompiler().compile(plan(
            step(1, "project.create", {"name": "One"}), second,
        ))


def test_wrong_result_kind_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="incompatible"):
        PlanCompiler().compile(plan(
            step(1, "note.create", {"title": "Ideas", "content": ""}),
            step(2, "task.create", {"title": "Task"}, result_of="step_1"),
        ))


def test_required_reference_source_is_rejected_when_absent() -> None:
    with pytest.raises(PlanValidationError, match="requires a reference"):
        PlanCompiler().compile(plan(
            step(1, "note.archive"),
            step(2, "project.create", {"name": "Safe"}),
        ))


def test_reference_on_reference_free_action_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="does not accept"):
        PlanCompiler().compile(plan(
            step(1, "project.create", {"name": "One"}, reference="Other"),
            step(2, "project.create", {"name": "Two"}),
        ))


def test_raw_uuid_reference_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="raw identifiers"):
        PlanCompiler().compile(plan(
            step(1, "note.archive", reference="2a72ba88-0cad-4d3b-a98a-266f1ecf90fe"),
            step(2, "project.create", {"name": "Safe"}),
        ))


def test_confirmation_policy_is_registry_derived() -> None:
    compiled = PlanCompiler().compile(plan(
        step(1, "note.create", {"title": "Old Ideas", "content": ""}),
        step(2, "note.archive", result_of="step_1", depends_on=["step_1"]),
    ))
    assert compiled.requires_confirmation is True


def test_model_plan_contract_is_discriminated_from_action_registry() -> None:
    variants = UNDERSTANDING_JSON_SCHEMA["properties"]["steps"]["items"]["anyOf"]
    assert [variant["properties"]["action"]["const"] for variant in variants] == list(
        ACTION_REGISTRY.names
    )
    for definition, variant in zip(ACTION_REGISTRY.definitions, variants, strict=True):
        arguments = variant["properties"]["arguments"]
        assert set(arguments["properties"]) == set(
            definition.argument_schema().get("properties", {})
        )
        assert arguments["additionalProperties"] is False


@pytest.mark.asyncio
async def test_executor_stops_after_first_failure_and_reports_partial() -> None:
    compiled = PlanCompiler().compile(plan(
        step(1, "project.create", {"name": "One"}),
        step(2, "project.create", {"name": "Two"}),
        step(3, "project.create", {"name": "Three"}),
    ))
    executed: list[str] = []

    async def prepare(current, references):
        return current.id, None

    async def execute(step_id, world):
        executed.append(step_id)
        if step_id == "step_2":
            return ActionResult(False, "project.create", "failed", failure_type="test")
        return ActionResult(
            True, "project.create", "created",
            references=(GroundedResultReference("project", None, step_id),),
        )

    async def remember(result):
        return None

    result = await PlanExecutor().execute(
        compiled, prepare_step=prepare, execute_step=execute,
        remember_result=remember,
    )

    assert result.status.value == "partial"
    assert result.failed_step == "step_2"
    assert [item.status for item in result.steps] == [
        "completed", "failed", "not_run"
    ]
    assert executed == ["step_1", "step_2"]
