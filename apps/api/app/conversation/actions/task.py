"""Task action argument contracts and definitions."""
from typing import Literal

from pydantic import Field

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class TaskListArgs(StrictActionArgs):
    pass


class TaskCreateArgs(StrictActionArgs):
    title: str = Field(min_length=1, max_length=255)


class TaskUpdateArgs(StrictActionArgs):
    status: Literal["complete"]


DEFINITIONS = (
    ActionDefinition(
        name="task.list", description="List active tasks, optionally in one project.",
        arguments=TaskListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.OPTIONAL,
        reference_kind="project", requires_world=True, grounder=ActionDomain.TASK, executor=ExecutorKey.TASK_LIST,
    ),
    ActionDefinition(
        name="task.create", description="Create one task in a named or grounded project.",
        arguments=TaskCreateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.CONTEXT_OR_REFERENCE, reference_kind="project",
        requires_world=True, grounder=ActionDomain.TASK, executor=ExecutorKey.TASK_CREATE,
    ),
    ActionDefinition(
        name="task.update", description="Mark one active task complete.",
        arguments=TaskUpdateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.REQUIRED, reference_kind="task",
        requires_world=True, grounder=ActionDomain.TASK, executor=ExecutorKey.TASK_UPDATE,
    ),
)
