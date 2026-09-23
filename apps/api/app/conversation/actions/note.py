"""Note action argument contracts and definitions."""
from typing import Literal

from pydantic import Field, model_validator

from app.conversation.actions.base import (
    ActionDefinition, ActionDomain, ActionMode, ConfirmationPolicy, ExecutorKey, ReferencePolicy,
    RiskLevel, StrictActionArgs,
)


class NoteCreateArgs(StrictActionArgs):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=20_000)


class NoteListArgs(StrictActionArgs):
    status: Literal["active", "archived"] | None = None


class NoteUpdateArgs(StrictActionArgs):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def require_change(self) -> "NoteUpdateArgs":
        if self.title is None and self.content is None:
            raise ValueError("note.update requires title or content")
        return self


class NoteArchiveArgs(StrictActionArgs):
    pass


DEFINITIONS = (
    ActionDefinition(
        name="note.create", description="Create one note with a title and optional content.",
        arguments=NoteCreateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.NONE, reference_kind=None, requires_world=False,
        grounder=ActionDomain.NOTE, executor=ExecutorKey.NOTE_CREATE,
    ),
    ActionDefinition(
        name="note.list", description="List active or archived notes.",
        arguments=NoteListArgs, mode=ActionMode.READ, risk=RiskLevel.READ,
        confirmation=ConfirmationPolicy.NONE, reference=ReferencePolicy.NONE,
        reference_kind=None, requires_world=False, grounder=ActionDomain.NOTE, executor=ExecutorKey.NOTE_LIST,
    ),
    ActionDefinition(
        name="note.update", description="Update the title or content of one active note.",
        arguments=NoteUpdateArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.LOW_RISK_WRITE, confirmation=ConfirmationPolicy.NONE,
        reference=ReferencePolicy.REQUIRED, reference_kind="note", requires_world=True,
        grounder=ActionDomain.NOTE, executor=ExecutorKey.NOTE_UPDATE,
    ),
    ActionDefinition(
        name="note.archive", description="Archive one active note.",
        arguments=NoteArchiveArgs, mode=ActionMode.MUTATION,
        risk=RiskLevel.DESTRUCTIVE_OR_REVERSAL,
        confirmation=ConfirmationPolicy.PLAN_STEP,
        reference=ReferencePolicy.REQUIRED, reference_kind="note", requires_world=True,
        grounder=ActionDomain.NOTE, executor=ExecutorKey.NOTE_ARCHIVE,
    ),
)
