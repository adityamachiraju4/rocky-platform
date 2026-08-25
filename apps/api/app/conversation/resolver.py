"""The resolver: message + WorldView -> ResolvedAction. Resolves only.

A resolver NEVER executes anything. It is a pure function of its inputs. This
is the swappable seam: v1 ships ``HardcodedResolver`` (deterministic,
intentionally dumb keyword matching); an ``LlmResolver`` can later implement
the same Protocol without moving the trust boundary, which lives in
ConversationService.

v1 contract (deliberately explicit, pinned by tests):

* Completion is VERB-GATED. A task-title match alone never triggers a
  mutation. One of the deterministic completion forms (``complete``,
  ``completed``, ``finish``, ``finished``, ``done``) must be present AND the
  message (verbs + safe filler stripped) must uniquely match a task
  title as a substring.
    - unique title match + verb  -> task.update{status: complete}
    - no title match             -> NoMatchError
    - 2+ title matches           -> AmbiguousReferenceError
* Reads are narrow keyword routes:
    - "projects" / "what am I working on" / "what's going on"
                                 -> project.list  (current state)
    - "what should I work on" / "anything lined up"
                                 -> task.list     (active task state)
    - "what did I do" / "where did we leave off" / "what changed"
                                 -> activity.recall  (history)
    - "tasks in <project-name>"  -> task.list for the uniquely matched project
* Anything else                  -> NoMatchError (honest miss, no execution)

The recall/project split is a product boundary, not a grammatical one:
recall answers "what happened", project.list answers "what exists now".
"what was I working on" -> recall; "what am I working on" -> project.list.

This is a testable dispatch skeleton, not language understanding.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol

from app.conversation import registry
from app.conversation.exceptions import (
    AmbiguousReferenceError,
    CompletionTargetNotFoundError,
    NoMatchError,
)
from app.conversation.schemas import ResolvedAction

COMPLETION_VERBS: frozenset[str] = frozenset(
    {"complete", "completed", "finish", "finished", "done"}
)
COMPLETION_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "as",
        "hey",
        "i",
        "is",
        "it",
        "mark",
        "ok",
        "okay",
        "rocky",
        "task",
        "the",
    }
)
PROJECT_LIST_CUES: tuple[str, ...] = (
    "what am i working on",
    "what's going on",
    "whats going on",
    "my projects",
    "list projects",
    "projects",
)
ACTIVE_TASK_CUES: tuple[str, ...] = (
    "anything lined up",
    "anything specific lined up",
    "anything i need to work on",
    "what should i work on",
    "what do i have going on",
    "do i have any active tasks",
    "any active tasks",
    "anything for me to do today",
    "anything to do today",
    "what do i have to do",
    "what do i need to do",
    "what tasks do i have",
)
ACTIVITY_RECALL_CUES: tuple[str, ...] = (
    "what did i do",
    "what have i been working on",
    "where did we leave off",
    "where did we leave things",
    "what was i working on",
    "what happened recently",
    "what did we do",
    "give me a recap",
    "recap",
    "catch me up",
    "what changed",
)
YESTERDAY_RECALL_CUES: tuple[str, ...] = (
    "what did i do yesterday",
    "what happened yesterday",
    "yesterday",
)


@dataclass(frozen=True)
class TaskRef:
    """A task flattened with its owning project, so a title match carries the
    project_id needed for update_task without a second lookup."""

    task_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    project_name: str
    status: str


@dataclass(frozen=True)
class ProjectRef:
    project_id: uuid.UUID
    name: str


@dataclass(frozen=True)
class ReminderRef:
    reminder_id: uuid.UUID
    title: str
    status: str
    due_at: datetime
    timezone: str


@dataclass(frozen=True)
class NotificationRef:
    notification_id: uuid.UUID
    title: str
    body: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class NoteRef:
    note_id: uuid.UUID
    title: str
    content: str
    status: str


@dataclass(frozen=True)
class ListItemRef:
    item_id: uuid.UUID
    content: str
    status: str


@dataclass(frozen=True)
class ListRef:
    list_id: uuid.UUID
    title: str
    status: str
    items: tuple[ListItemRef, ...] = ()


@dataclass(frozen=True)
class WorldView:
    """A read snapshot the resolver grounds against. Built by the service
    from the real domain services before the resolver runs."""

    projects: tuple[ProjectRef, ...]
    tasks: tuple[TaskRef, ...]
    reminders: tuple[ReminderRef, ...] = ()
    notifications: tuple[NotificationRef, ...] = ()
    notes: tuple[NoteRef, ...] = ()
    lists: tuple[ListRef, ...] = ()


class Resolver(Protocol):
    """The swappable brain. Resolves a message against the world. Executes
    nothing."""

    def resolve(self, message: str, world: WorldView) -> ResolvedAction: ...


class HardcodedResolver:
    """Deterministic keyword resolver. Intentionally dumb; throwaway once the
    LLM resolver lands. Proves the spine, not NLU."""

    def resolve(self, message: str, world: WorldView) -> ResolvedAction:
        text = message.strip().lower()
        tokens = _tokens(text)

        from app.reminders.interpretation import extract_reminder_intent

        project_create = re.fullmatch(
            (
                r"(?:create|make|start)\s+(?:a\s+|new\s+|a\s+new\s+)?"
                r"project\s+(?:called\s+|named\s+)?(.+)"
            ),
            message.strip(),
            re.IGNORECASE,
        )
        if project_create:
            return ResolvedAction(
                action=registry.PROJECT_CREATE,
                project_name=project_create.group(1).strip(" ."),
            )

        reminder_intent = extract_reminder_intent(message)
        if reminder_intent is not None:
            return ResolvedAction(
                action=registry.REMINDER_CREATE,
                reminder_title=reminder_intent.title,
                reminder_when=reminder_intent.when,
            )

        list_create = re.fullmatch(
            r"(?:create|make)\s+(?:me\s+)?a\s+(.+?)\s+list",
            message.strip(), re.IGNORECASE,
        )
        if list_create:
            return ResolvedAction(
                action=registry.LIST_CREATE,
                list_title=list_create.group(1).strip(" ."),
            )
        if any(cue in text for cue in ("show my lists", "show lists", "list my lists")):
            return ResolvedAction(action=registry.LIST_LIST)
        add_item = re.fullmatch(
            r"add\s+(.+?)\s+to\s+(?:my\s+|the\s+)?(.+?)\s+list",
            message.strip(), re.IGNORECASE,
        )
        if add_item:
            value = _resolve_list_title(add_item.group(2), world)
            return ResolvedAction(
                action=registry.LIST_ADD_ITEM, list_id=value.list_id,
                list_title=value.title, list_item_content=add_item.group(1).strip(" ."),
            )
        complete_item = re.fullmatch(
            r"mark\s+(.+?)\s+complete\s+(?:on|in)\s+(?:my\s+|the\s+)?(.+?)\s+list",
            message.strip(), re.IGNORECASE,
        )
        if complete_item:
            value = _resolve_list_title(complete_item.group(2), world)
            item = _resolve_list_item(complete_item.group(1), value)
            return ResolvedAction(
                action=registry.LIST_COMPLETE_ITEM, list_id=value.list_id,
                list_title=value.title, list_item_id=item.item_id,
                list_item_content=item.content,
            )
        archive_list = re.fullmatch(
            r"archive\s+(?:my\s+|the\s+)?(.+?)\s+list",
            message.strip(), re.IGNORECASE,
        )
        if archive_list:
            value = _resolve_list_title(archive_list.group(1), world)
            return ResolvedAction(
                action=registry.LIST_ARCHIVE, list_id=value.list_id,
                list_title=value.title,
            )

        task_create = _resolve_task_create(message, world)
        if task_create is not None:
            return task_create

        note_that = re.fullmatch(
            r"(?:make|create)\s+(?:me\s+)?a\s+note\s+that\s+(.+)",
            message.strip(),
            re.IGNORECASE,
        )
        if note_that:
            content = note_that.group(1).strip(" .")
            return ResolvedAction(
                action=registry.NOTE_CREATE,
                note_title=content[:120],
                note_content=content,
            )

        note_called = re.fullmatch(
            r"(?:make|create)\s+(?:me\s+)?a\s+note\s+(?:called|titled)\s+(.+)",
            message.strip(),
            re.IGNORECASE,
        )
        if note_called:
            return ResolvedAction(
                action=registry.NOTE_CREATE,
                note_title=note_called.group(1).strip(" ."),
                note_content="",
            )

        if any(
            cue in text
            for cue in ("show my notes", "show notes", "list notes", "my notes")
        ):
            return ResolvedAction(
                action=registry.NOTE_LIST,
                note_status="archived" if "archived" in tokens else "active",
            )

        note_archive = re.fullmatch(
            r"archive\s+(?:the\s+)?(.+?)\s+note",
            message.strip(),
            re.IGNORECASE,
        )
        if note_archive:
            note = _resolve_note_title(note_archive.group(1), world)
            return ResolvedAction(
                action=registry.NOTE_ARCHIVE,
                note_id=note.note_id,
                note_title=note.title,
            )

        note_update = re.fullmatch(
            r"update\s+(?:the\s+)?(.+?)\s+note\s+(?:to|with)\s+(.+)",
            message.strip(),
            re.IGNORECASE,
        )
        if note_update:
            note = _resolve_note_title(note_update.group(1), world)
            return ResolvedAction(
                action=registry.NOTE_UPDATE,
                note_id=note.note_id,
                note_title=note.title,
                note_content=note_update.group(2).strip(),
            )

        if "notification" in text and any(
            cue in text
            for cue in (
                "show my notifications",
                "show notifications",
                "what notifications do i have",
                "list notifications",
                "show unread notifications",
            )
        ):
            return ResolvedAction(
                action=registry.NOTIFICATION_LIST,
                notification_status="unread" if "unread" in tokens else None,
            )

        notification_action: str | None = None
        if "notification" in text and "dismiss" in tokens:
            notification_action = registry.NOTIFICATION_DISMISS
        elif "notification" in text and (
            "read" in tokens or "acknowledge" in tokens
        ):
            notification_action = registry.NOTIFICATION_READ
        if notification_action:
            ignored = {
                "acknowledge",
                "as",
                "dismiss",
                "mark",
                "notification",
                "my",
                "read",
                "rocky",
                "that",
                "the",
            }
            target = " ".join(token for token in tokens if token not in ignored)
            candidates = [
                item for item in world.notifications if item.status != "dismissed"
            ]
            matches = (
                [
                    item
                    for item in candidates
                    if target
                    and (
                        target in item.title.lower()
                        or item.title.lower() in target
                    )
                ]
                if target
                else candidates
            )
            if len(matches) == 1:
                return ResolvedAction(
                    action=notification_action,
                    notification_id=matches[0].notification_id,
                )
            if len(matches) > 1:
                raise AmbiguousReferenceError([item.title for item in matches])
            raise CompletionTargetNotFoundError(target or "that notification")

        if any(
            cue in text
            for cue in (
                "list reminders",
                "my reminders",
                "what are my reminders",
                "what reminders do i have",
            )
        ):
            return ResolvedAction(action=registry.REMINDER_LIST)

        reminder_action: str | None = None
        reminder_target = ""
        if "reminder" in text and any(
            cue in tokens for cue in ("cancel", "delete", "remove")
        ):
            reminder_action = registry.REMINDER_CANCEL
        elif "reminder" in text and any(
            cue in tokens for cue in COMPLETION_VERBS
        ):
            reminder_action = registry.REMINDER_COMPLETE
        if reminder_action:
            ignored = COMPLETION_VERBS | COMPLETION_STOPWORDS | {
                "cancel",
                "delete",
                "remove",
                "reminder",
                "my",
            }
            reminder_target = " ".join(
                token for token in tokens if token not in ignored
            )
            matches = [
                reminder
                for reminder in world.reminders
                if reminder.status in {"scheduled", "due"}
                and reminder_target
                and (
                    reminder_target in reminder.title.lower()
                    or reminder.title.lower() in reminder_target
                )
            ]
            if len(matches) == 1:
                return ResolvedAction(
                    action=reminder_action,
                    reminder_id=matches[0].reminder_id,
                    reminder_title=matches[0].title,
                )
            if len(matches) > 1:
                raise AmbiguousReferenceError([item.title for item in matches])
            raise CompletionTargetNotFoundError(reminder_target or "that reminder")

        # --- completion intent: verb-gated, phrase-in-title match ---
        if any(token in COMPLETION_VERBS for token in tokens):
            tokens = [
                w
                for w in tokens
                if w not in COMPLETION_VERBS and w not in COMPLETION_STOPWORDS
            ]
            phrase = " ".join(tokens).strip()
            if not phrase:
                raise NoMatchError(message)
            matches = [
                t
                for t in world.tasks
                if t.status == "active" and phrase in t.title.lower()
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
            raise CompletionTargetNotFoundError(phrase)

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

        # --- activity.recall: continuity / history cues ---
        # Product boundary, not grammar: recall answers "tell me what
        # happened"; project.list answers "tell me what exists now".
        # "what was i working on" -> recall; "what am i working on"
        # -> project.list. Checked before project.list so history
        # phrasing wins. Carries no args: the dispatch owns the window.
        if any(cue in text for cue in YESTERDAY_RECALL_CUES):
            return ResolvedAction(
                action=registry.ACTIVITY_RECALL,
                recall_window="yesterday",
            )

        if any(cue in text for cue in ACTIVITY_RECALL_CUES):
            return ResolvedAction(action=registry.ACTIVITY_RECALL)

        # --- task.list: current active task state ---
        if any(cue in text for cue in ACTIVE_TASK_CUES):
            return ResolvedAction(action=registry.TASK_LIST)

        # --- project.list: narrow cues ---
        if any(cue in text for cue in PROJECT_LIST_CUES):
            return ResolvedAction(action=registry.PROJECT_LIST)

        raise NoMatchError(message)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text)


def _resolve_task_create(message: str, world: WorldView) -> ResolvedAction | None:
    text = message.strip()
    patterns = (
        (
            r"(?:create|add)\s+a\s+task\s+called\s+(.+?)\s+in\s+(.+)",
            1,
            2,
        ),
        (
            r"add\s+a\s+task\s+to\s+(.+?)\s+called\s+(.+)",
            2,
            1,
        ),
        (
            r"create\s+(.+?)\s+under\s+(.+)",
            1,
            2,
        ),
        (
            r"add\s+(.+?)\s+to\s+the\s+(.+?)\s+project",
            1,
            2,
        ),
        (
            r"add\s+(.+?)\s+to\s+(.+)",
            1,
            2,
        ),
    )
    for pattern, title_group, project_group in patterns:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if not match:
            continue
        project = _resolve_project_name(match.group(project_group), world)
        return ResolvedAction(
            action=registry.TASK_CREATE,
            project_id=project.project_id,
            project_name=project.name,
            task_title=match.group(title_group).strip(" ."),
        )

    no_project = re.fullmatch(
        r"(?:create|add)\s+a\s+task\s+called\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if no_project:
        return ResolvedAction(
            action=registry.TASK_CREATE,
            task_title=no_project.group(1).strip(" ."),
        )
    return None


def _resolve_project_name(target: str, world: WorldView) -> ProjectRef:
    wanted = target.strip().lower()
    exact = [p for p in world.projects if p.name.lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    matches = [
        p
        for p in world.projects
        if wanted and (wanted in p.name.lower() or p.name.lower() in wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReferenceError([p.name for p in matches])
    raise CompletionTargetNotFoundError(wanted or "that project")


def _resolve_note_title(target: str, world: WorldView) -> NoteRef:
    wanted = target.strip().lower()
    matches = [
        note
        for note in world.notes
        if note.status == "active"
        and (wanted in note.title.lower() or note.title.lower() in wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReferenceError([note.title for note in matches])
    raise CompletionTargetNotFoundError(wanted or "that note")


def _resolve_list_title(target: str, world: WorldView) -> ListRef:
    wanted = target.strip().lower()
    matches = [
        value for value in world.lists
        if value.status == "active"
        and (wanted in value.title.lower() or value.title.lower() in wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReferenceError([value.title for value in matches])
    raise CompletionTargetNotFoundError(wanted or "that list")


def _resolve_list_item(target: str, value: ListRef) -> ListItemRef:
    wanted = target.strip().lower()
    matches = [
        item for item in value.items
        if item.status == "active"
        and (wanted in item.content.lower() or item.content.lower() in wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReferenceError([item.content for item in matches])
    raise CompletionTargetNotFoundError(wanted or "that list item")
