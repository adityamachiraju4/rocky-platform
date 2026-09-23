"""Activity action argument contracts and definitions."""
from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class ActivityRecallArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="activity.recall", description="Recall recent or local-calendar-yesterday activity.",
        arguments=ActivityRecallArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=True, grounder=ActionDomain.ACTIVITY,
        executor=ExecutorKey.ACTIVITY_RECALL,
    ),
)
