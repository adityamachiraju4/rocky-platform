"""Closed registry assembled from typed domain action definitions."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.conversation.actions import activity, list as list_actions, note
from app.conversation.actions import notification, project, reminder, task
from app.conversation.actions.base import ActionDefinition, StrictActionArgs
from app.conversation.exceptions import UnknownActionError


class ActionArgumentsError(ValueError):
    def __init__(self, action: str, error: ValidationError) -> None:
        super().__init__(f"Invalid arguments for {action}")
        self.action = action
        self.error = error


class ActionRegistry:
    def __init__(self, definitions: Iterable[ActionDefinition] = ()) -> None:
        self._definitions: dict[str, ActionDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ActionDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Duplicate action registration: {definition.name}")
        self._definitions[definition.name] = definition

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    @property
    def definitions(self) -> tuple[ActionDefinition, ...]:
        return tuple(self._definitions.values())

    def get(self, name: str) -> ActionDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise UnknownActionError(name) from exc

    def parse_arguments(
        self, name: str, arguments: dict[str, Any] | None
    ) -> StrictActionArgs:
        definition = self.get(name)
        try:
            return definition.arguments.model_validate(arguments or {})
        except ValidationError as exc:
            raise ActionArgumentsError(name, exc) from exc

    def model_catalog(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": definition.name,
                "description": definition.description,
                "arguments": definition.argument_schema(),
                "reference": definition.reference.value,
                "reference_kind": definition.reference_kind,
            }
            for definition in self.definitions
        )

    def model_prompt(self) -> str:
        return json.dumps(self.model_catalog(), separators=(",", ":"))


ACTION_REGISTRY = ActionRegistry(
    (
        *project.DEFINITIONS,
        *task.DEFINITIONS,
        *activity.DEFINITIONS,
        *reminder.DEFINITIONS,
        *notification.DEFINITIONS,
        *note.DEFINITIONS,
        *list_actions.DEFINITIONS,
    )
)

ACTION_NAMES = ACTION_REGISTRY.names
ALLOWED_ACTIONS = frozenset(ACTION_NAMES)

PROJECT_LIST = "project.list"
PROJECT_CREATE = "project.create"
TASK_LIST = "task.list"
TASK_CREATE = "task.create"
TASK_UPDATE = "task.update"
ACTIVITY_RECALL = "activity.recall"
REMINDER_CREATE = "reminder.create"
REMINDER_LIST = "reminder.list"
REMINDER_COMPLETE = "reminder.complete"
REMINDER_CANCEL = "reminder.cancel"
NOTIFICATION_LIST = "notification.list"
NOTIFICATION_READ = "notification.read"
NOTIFICATION_DISMISS = "notification.dismiss"
NOTE_CREATE = "note.create"
NOTE_LIST = "note.list"
NOTE_UPDATE = "note.update"
NOTE_ARCHIVE = "note.archive"
LIST_CREATE = "list.create"
LIST_LIST = "list.list"
LIST_ADD_ITEM = "list.add_item"
LIST_COMPLETE_ITEM = "list.complete_item"
LIST_ARCHIVE = "list.archive"


def is_allowed(action: str) -> bool:
    return action in ALLOWED_ACTIONS


def definition(action: str) -> ActionDefinition:
    return ACTION_REGISTRY.get(action)


def parse_arguments(action: str, arguments: dict[str, Any] | None) -> StrictActionArgs:
    return ACTION_REGISTRY.parse_arguments(action, arguments)


def model_catalog() -> tuple[dict[str, Any], ...]:
    return ACTION_REGISTRY.model_catalog()


__all__ = [
    "ACTION_NAMES", "ACTION_REGISTRY", "ALLOWED_ACTIONS", "ACTIVITY_RECALL",
    "LIST_ADD_ITEM", "LIST_ARCHIVE", "LIST_COMPLETE_ITEM", "LIST_CREATE",
    "LIST_LIST", "NOTE_ARCHIVE", "NOTE_CREATE", "NOTE_LIST", "NOTE_UPDATE",
    "NOTIFICATION_DISMISS", "NOTIFICATION_LIST", "NOTIFICATION_READ",
    "PROJECT_CREATE", "PROJECT_LIST", "REMINDER_CANCEL", "REMINDER_COMPLETE",
    "REMINDER_CREATE", "REMINDER_LIST", "TASK_CREATE", "TASK_LIST", "TASK_UPDATE",
    "ActionArgumentsError", "ActionRegistry", "definition", "is_allowed",
    "model_catalog", "parse_arguments",
]
