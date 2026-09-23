"""Core types for the closed Rocky action-definition registry."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict


class ActionMode(StrEnum):
    READ = "read"
    MUTATION = "mutation"


class ActionDomain(StrEnum):
    PROJECT = "project"
    TASK = "task"
    ACTIVITY = "activity"
    REMINDER = "reminder"
    NOTIFICATION = "notification"
    NOTE = "note"
    LIST = "list"


class ExecutorKey(StrEnum):
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


class RiskLevel(StrEnum):
    READ = "read"
    LOW_RISK_WRITE = "low_risk_write"
    DESTRUCTIVE_OR_REVERSAL = "destructive_or_reversal"
    SENSITIVE = "sensitive"


class ConfirmationPolicy(StrEnum):
    NONE = "none"
    PLAN_STEP = "plan_step"


class ReferencePolicy(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"
    CONTEXT_OR_REFERENCE = "context_or_reference"


class StrictActionArgs(BaseModel):
    """Base for model-proposed arguments; unknown/coerced values are refused."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, str_strip_whitespace=True
    )


ArgsT = TypeVar("ArgsT", bound=StrictActionArgs)


@dataclass(frozen=True)
class ActionDefinition(Generic[ArgsT]):
    name: str
    description: str
    arguments: type[ArgsT]
    mode: ActionMode
    risk: RiskLevel
    confirmation: ConfirmationPolicy
    reference: ReferencePolicy
    reference_kind: str | None
    requires_world: bool
    grounder: ActionDomain
    executor: ExecutorKey

    def argument_schema(self) -> dict[str, Any]:
        return self.arguments.model_json_schema()


@dataclass(frozen=True)
class GroundedResultReference:
    kind: str
    entity_id: uuid.UUID | None
    display_text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundedActionContext:
    """Rocky-produced internal references, never accepted from the model."""

    project_id: uuid.UUID | None = None
    project_name: str | None = None
    task_id: uuid.UUID | None = None
    task_title: str | None = None
    reminder_id: uuid.UUID | None = None
    reminder_title: str | None = None
    notification_id: uuid.UUID | None = None
    note_id: uuid.UUID | None = None
    note_title: str | None = None
    list_id: uuid.UUID | None = None
    list_title: str | None = None
    list_item_id: uuid.UUID | None = None
    list_item_content: str | None = None
    recall_window: str | None = None


@dataclass(frozen=True)
class ExecutableAction:
    """Registered, validated arguments paired with grounded execution context."""

    definition: ActionDefinition
    arguments: StrictActionArgs
    grounding: GroundedActionContext


@dataclass(frozen=True)
class ActionResult:
    """Consistent internal action result; the HTTP API still receives prose."""

    success: bool
    action: str
    display_summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    entity_ids: tuple[uuid.UUID, ...] = ()
    references: tuple[GroundedResultReference, ...] = ()
    failure_type: str | None = None
    outcome: Any | None = None
