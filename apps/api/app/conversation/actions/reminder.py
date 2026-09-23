"""Reminder action argument contracts and definitions."""
from pydantic import Field

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class ReminderCreateArgs(StrictActionArgs):
    title: str = Field(min_length=1, max_length=500)
    when: str = Field(min_length=1, max_length=500)


class ReminderListArgs(StrictActionArgs):
    pass


class ReminderStateArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="reminder.create", description="Create one reminder from explicit time wording.",
        arguments=ReminderCreateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.NONE, reference_kind=None, requires_world=False,
        grounder=ActionDomain.REMINDER, executor=ExecutorKey.REMINDER_CREATE,
    ),
    ActionDefinition(
        name="reminder.list", description="List open reminders.",
        arguments=ReminderListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=False, grounder=ActionDomain.REMINDER,
        executor=ExecutorKey.REMINDER_LIST,
    ),
    ActionDefinition(
        name="reminder.complete", description="Complete one open reminder.",
        arguments=ReminderStateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.REQUIRED, reference_kind="reminder",
        requires_world=True, grounder=ActionDomain.REMINDER, executor=ExecutorKey.REMINDER_COMPLETE,
    ),
    ActionDefinition(
        name="reminder.cancel", description="Cancel one open reminder.",
        arguments=ReminderStateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.DESTRUCTIVE_OR_REVERSAL,
        confirmation=ConfirmationPolicy.PLAN_STEP,
        reference=ReferencePolicy.REQUIRED, reference_kind="reminder",
        requires_world=True, grounder=ActionDomain.REMINDER, executor=ExecutorKey.REMINDER_CANCEL,
    ),
)
