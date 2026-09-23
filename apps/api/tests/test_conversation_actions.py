"""Typed closed-registry contracts for Conversation actions."""
from dataclasses import replace

import pytest

from app.conversation import registry
from app.conversation.actions.base import (
    ActionMode,
    ConfirmationPolicy,
    ExecutorKey,
    ReferencePolicy,
    RiskLevel,
)
from app.conversation.actions.project import ProjectCreateArgs
from app.conversation.actions.registry import (
    ACTION_REGISTRY,
    ActionArgumentsError,
    ActionRegistry,
)
from app.conversation.actions.runtime import (
    ARGUMENT_ADAPTERS,
    validate_resolved_action,
)
from app.conversation.exceptions import UnknownActionError
from app.conversation.schemas import ResolvedAction
from app.conversation.service import ACTION_EXECUTOR_METHODS, ConversationService
from app.conversation.understanding import UNDERSTANDING_JSON_SCHEMA


EXPECTED_ACTIONS = (
    "project.list",
    "project.create",
    "task.list",
    "task.create",
    "task.update",
    "activity.recall",
    "reminder.create",
    "reminder.list",
    "reminder.complete",
    "reminder.cancel",
    "notification.list",
    "notification.read",
    "notification.dismiss",
    "note.create",
    "note.list",
    "note.update",
    "note.archive",
    "list.create",
    "list.list",
    "list.add_item",
    "list.complete_item",
    "list.archive",
)


def test_all_expected_actions_are_registered_once() -> None:
    assert ACTION_REGISTRY.names == EXPECTED_ACTIONS
    assert len(ACTION_REGISTRY.names) == len(set(ACTION_REGISTRY.names))
    assert registry.ACTION_NAMES == EXPECTED_ACTIONS


def test_duplicate_registration_is_rejected() -> None:
    definition = ACTION_REGISTRY.get("project.list")
    duplicate_registry = ActionRegistry((definition,))

    with pytest.raises(ValueError, match="Duplicate action registration"):
        duplicate_registry.register(replace(definition))


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(UnknownActionError):
        ACTION_REGISTRY.get("system.delete_everything")
    with pytest.raises(UnknownActionError):
        ACTION_REGISTRY.parse_arguments("system.delete_everything", {})


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("project.create", {"name": "Japan Trip"}),
        ("task.create", {"title": "Book flights"}),
        ("task.update", {"status": "complete"}),
        ("reminder.create", {"title": "Call Rahul", "when": "7 tonight"}),
        ("notification.list", {"status": "unread"}),
        ("note.create", {"title": "Website", "content": "Use graphite"}),
        ("note.update", {"content": "Use calmer spacing"}),
        ("list.add_item", {"content": "Milk"}),
        ("list.complete_item", {"item": "Milk"}),
    ],
)
def test_valid_typed_arguments_are_accepted(
    action: str, arguments: dict[str, str]
) -> None:
    parsed = ACTION_REGISTRY.parse_arguments(action, arguments)
    assert parsed.model_dump(exclude_none=True)


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("project.create", {}),
        ("project.create", {"name": "Trip", "owner": "someone"}),
        ("project.create", {"name": 42}),
        ("task.update", {"status": "deleted"}),
        ("reminder.create", {"title": "Call Rahul"}),
        ("note.update", {}),
        ("list.add_item", {"content": ""}),
    ],
)
def test_invalid_typed_arguments_are_rejected(
    action: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(ActionArgumentsError):
        ACTION_REGISTRY.parse_arguments(action, arguments)


def test_every_action_has_complete_policy_and_execution_metadata() -> None:
    for definition in ACTION_REGISTRY.definitions:
        assert definition.description
        assert definition.mode in ActionMode
        assert definition.risk in RiskLevel
        assert definition.confirmation in ConfirmationPolicy
        assert definition.reference in ReferencePolicy
        assert definition.grounder.value == definition.name.partition(".")[0]
        assert definition.executor.value == definition.name


def test_read_and_write_risk_policy_is_consistent() -> None:
    reads = {definition.name for definition in ACTION_REGISTRY.definitions
             if definition.mode is ActionMode.READ}
    assert reads == {
        "project.list", "task.list", "activity.recall", "reminder.list",
        "notification.list", "note.list", "list.list",
    }
    for definition in ACTION_REGISTRY.definitions:
        if definition.mode is ActionMode.READ:
            assert definition.risk is RiskLevel.READ
            assert definition.confirmation is ConfirmationPolicy.NONE
        else:
            assert definition.risk is not RiskLevel.READ


def test_reversal_actions_are_visible_to_ci4_confirmation_policy() -> None:
    for name in {
        "reminder.cancel", "notification.dismiss", "note.archive", "list.archive"
    }:
        definition = ACTION_REGISTRY.get(name)
        assert definition.risk is RiskLevel.DESTRUCTIVE_OR_REVERSAL
        assert definition.confirmation is ConfirmationPolicy.PLAN_STEP


def test_model_schema_and_catalog_derive_from_registry() -> None:
    action_schema = UNDERSTANDING_JSON_SCHEMA["properties"]["action"]
    assert action_schema["enum"] == [*ACTION_REGISTRY.names, None]
    catalog = ACTION_REGISTRY.model_catalog()
    assert tuple(entry["name"] for entry in catalog) == ACTION_REGISTRY.names
    for definition, entry in zip(ACTION_REGISTRY.definitions, catalog, strict=True):
        assert entry["arguments"] == definition.argument_schema()
        assert entry["reference"] == definition.reference.value


def test_runtime_adapter_invokes_registered_typed_validation(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    original = ACTION_REGISTRY.parse_arguments

    def spy(action: str, arguments: dict[str, object] | None):
        calls.append((action, arguments or {}))
        return original(action, arguments)

    monkeypatch.setattr(ACTION_REGISTRY, "parse_arguments", spy)
    executable = validate_resolved_action(
        ResolvedAction(
            action=registry.PROJECT_CREATE,
            project_name="  Japan Trip  ",
        )
    )

    assert calls == [(registry.PROJECT_CREATE, {"name": "  Japan Trip  "})]
    assert isinstance(executable.arguments, ProjectCreateArgs)
    assert executable.arguments.name == "Japan Trip"


@pytest.mark.parametrize(
    "action",
    [
        ResolvedAction(action=registry.PROJECT_CREATE),
        ResolvedAction(action=registry.TASK_UPDATE, status="deleted"),
        ResolvedAction(action=registry.REMINDER_CREATE, reminder_title="Call"),
    ],
)
def test_runtime_adapter_rejects_invalid_legacy_actions(
    action: ResolvedAction,
) -> None:
    with pytest.raises(ActionArgumentsError):
        validate_resolved_action(action)


def test_every_definition_has_one_bounded_runtime_route() -> None:
    executor_keys = {definition.executor for definition in ACTION_REGISTRY.definitions}
    assert executor_keys == set(ExecutorKey)
    assert set(ARGUMENT_ADAPTERS) == executor_keys
    assert set(ACTION_EXECUTOR_METHODS) == executor_keys
    assert len(set(ACTION_EXECUTOR_METHODS.values())) == len(executor_keys)
    for method_name in ACTION_EXECUTOR_METHODS.values():
        assert callable(getattr(ConversationService, method_name))
