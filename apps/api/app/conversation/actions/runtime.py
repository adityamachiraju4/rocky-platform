"""Authoritative adapter from legacy resolved actions to typed execution."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.conversation.actions.base import (
    ExecutableAction,
    ExecutorKey,
    GroundedActionContext,
)
from app.conversation.actions.registry import ACTION_REGISTRY
from app.conversation.exceptions import UnknownActionError
from app.conversation.schemas import ResolvedAction


def _empty(_: ResolvedAction) -> dict[str, Any]:
    return {}


def _without_none(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


ARGUMENT_ADAPTERS: dict[
    ExecutorKey, Callable[[ResolvedAction], dict[str, Any]]
] = {
    ExecutorKey.PROJECT_LIST: _empty,
    ExecutorKey.PROJECT_CREATE: lambda action: {"name": action.project_name},
    ExecutorKey.TASK_LIST: _empty,
    ExecutorKey.TASK_CREATE: lambda action: {"title": action.task_title},
    ExecutorKey.TASK_UPDATE: lambda action: {"status": action.status},
    ExecutorKey.ACTIVITY_RECALL: _empty,
    ExecutorKey.REMINDER_CREATE: lambda action: {
        "title": action.reminder_title,
        "when": action.reminder_when,
    },
    ExecutorKey.REMINDER_LIST: _empty,
    ExecutorKey.REMINDER_COMPLETE: _empty,
    ExecutorKey.REMINDER_CANCEL: _empty,
    ExecutorKey.NOTIFICATION_LIST: lambda action: _without_none(
        status=action.notification_status
    ),
    ExecutorKey.NOTIFICATION_READ: _empty,
    ExecutorKey.NOTIFICATION_DISMISS: _empty,
    ExecutorKey.NOTE_CREATE: lambda action: {
        "title": action.note_title,
        "content": action.note_content or "",
    },
    ExecutorKey.NOTE_LIST: lambda action: _without_none(
        status=action.note_status
    ),
    ExecutorKey.NOTE_UPDATE: lambda action: _without_none(
        title=action.note_title, content=action.note_content
    ),
    ExecutorKey.NOTE_ARCHIVE: _empty,
    ExecutorKey.LIST_CREATE: lambda action: {"title": action.list_title},
    ExecutorKey.LIST_LIST: _empty,
    ExecutorKey.LIST_ADD_ITEM: lambda action: {
        "content": action.list_item_content
    },
    ExecutorKey.LIST_COMPLETE_ITEM: lambda action: {
        "item": action.list_item_content
    },
    ExecutorKey.LIST_ARCHIVE: _empty,
}


def validate_resolved_action(action: ResolvedAction) -> ExecutableAction:
    """Fail closed, then separate validated arguments from grounded IDs."""

    definition = ACTION_REGISTRY.get(action.action)
    if definition.executor.value != action.action:
        raise UnknownActionError(action.action)
    try:
        adapter = ARGUMENT_ADAPTERS[definition.executor]
    except KeyError as exc:  # pragma: no cover - registry construction guard
        raise UnknownActionError(action.action) from exc
    arguments = ACTION_REGISTRY.parse_arguments(
        action.action, adapter(action)
    )
    grounding = GroundedActionContext(
        project_id=action.project_id,
        project_name=action.project_name,
        task_id=action.task_id,
        task_title=action.task_title,
        reminder_id=action.reminder_id,
        reminder_title=action.reminder_title,
        notification_id=action.notification_id,
        note_id=action.note_id,
        note_title=action.note_title,
        list_id=action.list_id,
        list_title=action.list_title,
        list_item_id=action.list_item_id,
        list_item_content=action.list_item_content,
        recall_window=action.recall_window,
    )
    return ExecutableAction(
        definition=definition,
        arguments=arguments,
        grounding=grounding,
    )
