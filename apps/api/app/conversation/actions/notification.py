"""Notification action argument contracts and definitions."""
from typing import Literal

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class NotificationListArgs(StrictActionArgs):
    status: Literal["unread"] | None = None


class NotificationStateArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="notification.list", description="List visible notifications, optionally unread only.",
        arguments=NotificationListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=False, grounder=ActionDomain.NOTIFICATION,
        executor=ExecutorKey.NOTIFICATION_LIST,
    ),
    ActionDefinition(
        name="notification.read", description="Mark one owned notification read.",
        arguments=NotificationStateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.OPTIONAL, reference_kind="notification",
        requires_world=True, grounder=ActionDomain.NOTIFICATION, executor=ExecutorKey.NOTIFICATION_READ,
    ),
    ActionDefinition(
        name="notification.dismiss", description="Dismiss one owned notification.",
        arguments=NotificationStateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.DESTRUCTIVE_OR_REVERSAL,
        confirmation=ConfirmationPolicy.PLAN_STEP,
        reference=ReferencePolicy.OPTIONAL, reference_kind="notification",
        requires_world=True, grounder=ActionDomain.NOTIFICATION, executor=ExecutorKey.NOTIFICATION_DISMISS,
    ),
)
