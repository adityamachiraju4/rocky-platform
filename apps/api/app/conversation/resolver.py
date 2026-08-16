"""The resolver: message + WorldView -> ResolvedAction. Resolves only.

A resolver NEVER executes anything. It is a pure function of its inputs. This
is the swappable seam: v1 ships ``HardcodedResolver`` (deterministic,
intentionally dumb keyword matching); an ``LlmResolver`` can later implement
the same Protocol without moving the trust boundary, which lives in
ConversationService.

v1 contract (deliberately explicit, pinned by tests):

* Completion is VERB-GATED. A task-title match alone never triggers a
  mutation. One of ``complete`` / ``finish`` / ``done`` must be present AND a
  the message (verbs + stopwords stripped) must uniquely match a task
  title as a substring.
    - unique title match + verb  -> task.update{status: complete}
    - no title match             -> NoMatchError
    - 2+ title matches           -> AmbiguousReferenceError
* Reads are narrow keyword routes:
    - "projects" / "what am I working on" / "what's going on"
                                 -> project.list
    - "tasks in <project-name>"  -> task.list for the uniquely matched project
* Anything else                  -> NoMatchError (honest miss, no execution)

This is a testable dispatch skeleton, not language understanding.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.conversation import registry
from app.conversation.exceptions import (
    AmbiguousReferenceError,
    NoMatchError,
)
from app.conversation.schemas import ResolvedAction

COMPLETION_VERBS: tuple[str, ...] = ("complete", "finish", "done")
COMPLETION_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "task", "as", "mark"}
)
PROJECT_LIST_CUES: tuple[str, ...] = (
    "what am i working on",
    "what's going on",
    "whats going on",
    "my projects",
    "list projects",
    "projects",
)


@dataclass(frozen=True)
class TaskRef:
    """A task flattened with its owning project, so a title match carries the
    project_id needed for update_task without a second lookup."""

    task_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    project_name: str


@dataclass(frozen=True)
class ProjectRef:
    project_id: uuid.UUID
    name: str


@dataclass(frozen=True)
class WorldView:
    """A read snapshot the resolver grounds against. Built by the service
    from the real domain services before the resolver runs."""

    projects: tuple[ProjectRef, ...]
    tasks: tuple[TaskRef, ...]


class Resolver(Protocol):
    """The swappable brain. Resolves a message against the world. Executes
    nothing."""

    def resolve(self, message: str, world: WorldView) -> ResolvedAction: ...


class HardcodedResolver:
    """Deterministic keyword resolver. Intentionally dumb; throwaway once the
    LLM resolver lands. Proves the spine, not NLU."""

    def resolve(self, message: str, world: WorldView) -> ResolvedAction:
        text = message.strip().lower()

        # --- completion intent: verb-gated, phrase-in-title match ---
        if any(verb in text for verb in COMPLETION_VERBS):
            tokens = [
                w
                for w in text.split()
                if w not in COMPLETION_VERBS and w not in COMPLETION_STOPWORDS
            ]
            phrase = " ".join(tokens).strip()
            if not phrase:
                raise NoMatchError(message)
            matches = [
                t for t in world.tasks if phrase in t.title.lower()
            ]
            if len(matches) == 1:
                m = matches[0]
                return ResolvedAction(
                    action=registry.TASK_UPDATE,
                    project_id=m.project_id,
                    task_id=m.task_id,
                    status="complete",
                )
            if len(matches) > 1:
                raise AmbiguousReferenceError(
                    [f"{m.title} ({m.project_name})" for m in matches]
                )
            # verb present, no title matched -> honest miss
            raise NoMatchError(message)

        # --- task.list: "tasks in <project>" ---
        if "tasks in " in text:
            wanted = text.split("tasks in ", 1)[1].strip()
            if wanted:
                pmatches = [
                    p for p in world.projects if p.name.lower() in wanted
                    or wanted in p.name.lower()
                ]
                if len(pmatches) == 1:
                    return ResolvedAction(
                        action=registry.TASK_LIST,
                        project_id=pmatches[0].project_id,
                    )
                if len(pmatches) > 1:
                    raise AmbiguousReferenceError(
                        [p.name for p in pmatches]
                    )
            raise NoMatchError(message)

        # --- project.list: narrow cues ---
        if any(cue in text for cue in PROJECT_LIST_CUES):
            return ResolvedAction(action=registry.PROJECT_LIST)

        raise NoMatchError(message)
