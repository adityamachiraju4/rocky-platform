"""Project action argument contracts and definitions."""
from pydantic import Field

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class ProjectListArgs(StrictActionArgs):
    pass


class ProjectCreateArgs(StrictActionArgs):
    name: str = Field(min_length=1, max_length=255)


DEFINITIONS = (
    ActionDefinition(
        name="project.list", description="List the authenticated user's projects.",
        arguments=ProjectListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=False, grounder=ActionDomain.PROJECT,
        executor=ExecutorKey.PROJECT_LIST,
    ),
    ActionDefinition(
        name="project.create", description="Create one project with a name.",
        arguments=ProjectCreateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.NONE, reference_kind=None,
        requires_world=False, grounder=ActionDomain.PROJECT, executor=ExecutorKey.PROJECT_CREATE,
    ),
)
