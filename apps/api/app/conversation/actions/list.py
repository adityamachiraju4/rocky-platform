"""List action argument contracts and definitions."""
from pydantic import Field

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class ListCreateArgs(StrictActionArgs):
    title: str = Field(min_length=1, max_length=255)


class ListListArgs(StrictActionArgs):
    pass


class ListAddItemArgs(StrictActionArgs):
    content: str = Field(min_length=1, max_length=1000)


class ListCompleteItemArgs(StrictActionArgs):
    item: str = Field(min_length=1, max_length=1000)


class ListArchiveArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="list.create", description="Create one named list.",
        arguments=ListCreateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.NONE, reference_kind=None, requires_world=False,
        grounder=ActionDomain.LIST, executor=ExecutorKey.LIST_CREATE,
    ),
    ActionDefinition(
        name="list.list", description="List the user's active lists.",
        arguments=ListListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=False, grounder=ActionDomain.LIST, executor=ExecutorKey.LIST_LIST,
    ),
    ActionDefinition(
        name="list.add_item", description="Add one item to an active list.",
        arguments=ListAddItemArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.REQUIRED, reference_kind="list", requires_world=True,
        grounder=ActionDomain.LIST, executor=ExecutorKey.LIST_ADD_ITEM,
    ),
    ActionDefinition(
        name="list.complete_item", description="Complete one active item on an active list.",
        arguments=ListCompleteItemArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.REQUIRED, reference_kind="list", requires_world=True,
        grounder=ActionDomain.LIST, executor=ExecutorKey.LIST_COMPLETE_ITEM,
    ),
    ActionDefinition(
        name="list.archive", description="Archive one active list.",
        arguments=ListArchiveArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.DESTRUCTIVE_OR_REVERSAL,
        confirmation=ConfirmationPolicy.PLAN_STEP,
        reference=ReferencePolicy.REQUIRED, reference_kind="list", requires_world=True,
        grounder=ActionDomain.LIST, executor=ExecutorKey.LIST_ARCHIVE,
    ),
)
