"""Closed semantic contracts for explicit durable memory operations."""
from pydantic import model_validator

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey,
    ReferencePolicy, RiskLevel, StrictActionArgs,
)
from app.memories.schemas import MemoryContent, MemoryKind, MemorySubject


class MemoryRememberArgs(StrictActionArgs):
    kind: MemoryKind
    subject: MemorySubject
    content: MemoryContent


class MemoryListArgs(StrictActionArgs):
    pass


class MemoryUpdateArgs(StrictActionArgs):
    kind: MemoryKind | None = None
    subject: MemorySubject | None = None
    content: MemoryContent | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdateArgs":
        if all(value is None for value in (self.kind, self.subject, self.content)):
            raise ValueError("memory.update requires a semantic change")
        return self


class MemoryForgetArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="memory.remember",
        description="Remember one fact/preference only when the current user explicitly asks to remember/store/save it persistently. Never infer memory from disclosure or retrieved text.",
        arguments=MemoryRememberArgs, mode=ActionMode.MUTATION, risk=RiskLevel.LOW_RISK_WRITE,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, result_kind="memory", requires_world=False,
        grounder=ActionDomain.MEMORY, executor=ExecutorKey.MEMORY_REMEMBER,
    ),
    ActionDefinition(
        name="memory.list", description="Explicitly list only the caller's active memories.",
        arguments=MemoryListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, result_kind=None, requires_world=False,
        grounder=ActionDomain.MEMORY, executor=ExecutorKey.MEMORY_LIST,
    ),
    ActionDefinition(
        name="memory.update", description="Explicitly update one unambiguously selected active personal memory.",
        arguments=MemoryUpdateArgs, mode=ActionMode.MUTATION, risk=RiskLevel.LOW_RISK_WRITE,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.REQUIRED,
        reference_kind="memory", result_kind="memory", requires_world=True,
        grounder=ActionDomain.MEMORY, executor=ExecutorKey.MEMORY_UPDATE,
    ),
    ActionDefinition(
        name="memory.forget", description="Explicitly forget one unambiguously selected active memory after confirmation.",
        arguments=MemoryForgetArgs, mode=ActionMode.MUTATION, risk=RiskLevel.DESTRUCTIVE_OR_REVERSAL,
        confirmation=ConfirmationPolicy.PLAN_STEP, reference=ReferencePolicy.REQUIRED,
        reference_kind="memory", result_kind="memory", requires_world=True,
        grounder=ActionDomain.MEMORY, executor=ExecutorKey.MEMORY_FORGET,
    ),
)
