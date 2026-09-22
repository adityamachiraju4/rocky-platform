"""The Responder seam: turns a structured Outcome into Rocky's words.

This is the deliberate separation of *what happened* from *how Rocky says it*.
``ConversationService`` dispatches an action and produces an :class:`Outcome`
-- a typed record of the real result (the actual returned domain object, or
the real ledger facts). A ``Responder`` then renders that Outcome into the
reply string.

v1 ships :class:`TemplateResponder`: deterministic, grounded, no inference.
It states only what the Outcome contains. A later ``LlmResponder`` can
implement the same Protocol and phrase things naturally -- but it receives an
Outcome (facts) and returns words; it is asked "how should Rocky say this?",
never "what should Rocky do?". Execution authority stays with the resolver's
trust boundary in the service. The Responder only reads facts and speaks.

Grounded-facts rule (v1, pinned by tests): recall narrates the ledger events
it was given, and nothing more. No "you were mainly focused on X", no "you
should do Y next" -- those are reasoning/initiative behaviors reserved for the
LlmResponder. The template says what happened; it does not interpret.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
import uuid

from app.core.time import UTC, utc_to_local

# Outcome kinds. Each maps to one rendering branch in the responder.
OutcomeKind = Literal[
    "project_list",
    "project_created",
    "task_list",
    "task_status",
    "task_created",
    "task_updated",
    "activity_recall",
    "reminder_created",
    "reminder_list",
    "reminder_updated",
    "notification_list",
    "notification_updated",
    "note_created",
    "note_list",
    "note_updated",
    "list_created",
    "list_list",
    "list_item_added",
    "list_item_completed",
    "list_archived",
    "conversation",
    "no_match",
    "target_not_found",
    "ambiguous",
    "unsupported",
]


@dataclass(frozen=True)
class RecallFact:
    """One narratable ledger event: the event type and a human label for the
    entity it concerned. Built by the dispatch from real Activity rows."""

    event_type: str
    entity_type: str
    entity_label: str | None


@dataclass(frozen=True)
class Outcome:
    """The structured result of one turn, before it becomes words.

    ``kind`` selects the rendering branch. The other fields carry the real
    facts for that branch -- only the ones relevant to ``kind`` are set. This
    is what the dispatch returns and what a Responder consumes; it never
    crosses the HTTP boundary (the service wraps the rendered reply in a
    ConversationResponse).
    """

    kind: OutcomeKind
    executed: bool
    action: str | None = None
    # project_list
    project_names: tuple[str, ...] = ()
    project_name: str | None = None
    # task_list
    task_total: int = 0
    task_active: int = 0
    task_scope: Literal["project", "all"] = "project"
    task_titles: tuple[str, ...] = ()
    latest_completed_task_title: str | None = None
    # task_status
    # task_updated
    task_title: str | None = None
    task_status: str | None = None
    # activity_recall
    recall_facts: tuple[RecallFact, ...] = ()
    recall_window: Literal["recent", "yesterday"] = "recent"
    # reminders
    reminder_title: str | None = None
    reminder_due_at: datetime | None = None
    reminder_timezone: str | None = None
    reminder_status: str | None = None
    reminders: tuple[tuple[str, datetime, str, str], ...] = ()
    # notifications
    notifications: tuple[tuple[str, str, str], ...] = ()
    notification_title: str | None = None
    notification_status: str | None = None
    # notes
    notes: tuple[tuple[str, str], ...] = ()
    note_title: str | None = None
    note_status: str | None = None
    # lists
    list_title: str | None = None
    list_titles: tuple[str, ...] = ()
    list_item_content: str | None = None
    # no_match / ambiguous
    candidates: tuple[str, ...] = ()
    target: str | None = None
    target_type: Literal[
        "project", "task", "reminder", "notification", "note", "list"
    ] = "task"
    reply: str | None = None
    # Durable conversational grounding; ids are revalidated against the
    # authoritative owned WorldView before later mutations.
    reference_kind: str | None = None
    reference_id: uuid.UUID | None = None
    reference_label: str | None = None
    reference_metadata: dict[str, str] | None = None


class Responder(Protocol):
    """Renders an Outcome into a reply string. Reads facts, returns words.
    Holds no execution authority."""

    def render(self, outcome: Outcome, *, language: str = "en") -> str: ...


def _quoted(value: str | None) -> str:
    return f"'{value}'" if value else ""


def _recall_phrase(fact: RecallFact) -> str:
    label = _quoted(fact.entity_label)

    if fact.event_type == "project.created":
        return f"created the {label} project" if label else "created a project"
    if fact.event_type == "project.updated":
        return f"updated the {label} project" if label else "updated a project"
    if fact.event_type == "task.created":
        return f"created the task {label}" if label else "created a task"
    if fact.event_type == "task.completed":
        return f"completed {label}" if label else "completed a task"
    if fact.event_type == "task.updated":
        return f"updated the task {label}" if label else "updated a task"
    if fact.event_type == "note.created":
        return f"created the note {label}" if label else "created a note"
    if fact.event_type == "note.updated":
        return f"updated the note {label}" if label else "updated a note"
    if fact.event_type == "note.archived":
        return f"archived the note {label}" if label else "archived a note"
    if fact.event_type == "list.created":
        return f"created the list {label}" if label else "created a list"
    if fact.event_type == "list.archived":
        return f"archived the list {label}" if label else "archived a list"
    if fact.event_type == "list.item_added":
        return f"added {label} to a list" if label else "added a list item"
    if fact.event_type == "list.item_completed":
        return f"completed {label} on a list" if label else "completed a list item"

    if fact.entity_type == "project":
        return f"updated the {label} project" if label else "updated a project"
    if fact.entity_type == "task":
        return f"updated the task {label}" if label else "updated a task"
    if fact.entity_type == "note":
        return f"updated the note {label}" if label else "updated a note"
    return "recorded activity"


def _localized_capability_reply(
    outcome: Outcome, language: str
) -> str | None:
    if language == "en":
        return None

    if outcome.kind == "task_list":
        titles = ", ".join(_quoted(title) for title in outcome.task_titles)
        if outcome.task_titles:
            templates = {
                "hi": "आपके {count} सक्रिय काम हैं: {titles}।",
                "te": "మీకు {count} యాక్టివ్ పనులు ఉన్నాయి: {titles}.",
                "ta": "உங்களுக்கு {count} செயலில் உள்ள வேலைகள் உள்ளன: {titles}.",
                "es": "Tienes {count} tareas activas: {titles}.",
                "fr": "Vous avez {count} tâches actives : {titles}.",
                "ja": "進行中のタスクは{count}件です: {titles}。",
            }
            template = templates.get(language)
            if template:
                return template.format(count=len(outcome.task_titles), titles=titles)
        empty = {
            "hi": "अभी आपका कोई सक्रिय काम नहीं है।",
            "te": "ప్రస్తుతం మీకు యాక్టివ్ పనులు లేవు.",
            "ta": "இப்போது உங்களிடம் செயலில் உள்ள வேலைகள் இல்லை.",
            "es": "No tienes tareas activas ahora mismo.",
            "fr": "Vous n'avez aucune tâche active pour le moment.",
            "ja": "現在、進行中のタスクはありません。",
        }
        return empty.get(language)

    if outcome.kind == "project_list":
        names = ", ".join(outcome.project_names)
        if outcome.project_names:
            templates = {
                "hi": "आपके {count} प्रोजेक्ट हैं: {names}।",
                "te": "మీకు {count} ప్రాజెక్టులు ఉన్నాయి: {names}.",
                "ta": "உங்களிடம் {count} திட்டங்கள் உள்ளன: {names}.",
                "es": "Tienes {count} proyectos: {names}.",
                "fr": "Vous avez {count} projets : {names}.",
                "ja": "プロジェクトは{count}件です: {names}。",
            }
            template = templates.get(language)
            if template:
                return template.format(count=len(outcome.project_names), names=names)
        empty = {
            "hi": "अभी आपका कोई प्रोजेक्ट नहीं है।",
            "te": "ప్రస్తుతం మీకు ప్రాజెక్టులు లేవు.",
            "ta": "இப்போது உங்களிடம் திட்டங்கள் இல்லை.",
            "es": "Todavía no tienes proyectos.",
            "fr": "Vous n'avez pas encore de projets.",
            "ja": "まだプロジェクトはありません。",
        }
        return empty.get(language)

    return None


class TemplateResponder:
    """Deterministic, grounded renderer. Says exactly what the Outcome holds;
    interprets nothing. Throwaway once LlmResponder lands behind the Protocol.
    """

    def render(self, outcome: Outcome, *, language: str = "en") -> str:
        localized = _localized_capability_reply(outcome, language)
        if localized is not None:
            return localized
        if outcome.kind == "project_list":
            if not outcome.project_names:
                return "You have no projects yet."
            names = ", ".join(outcome.project_names)
            n = len(outcome.project_names)
            return f"You have {n} project(s): {names}."

        if outcome.kind == "project_created":
            return f"Created the {_quoted(outcome.project_name)} project."

        if outcome.kind == "task_list":
            if outcome.task_scope == "project" and outcome.task_total == 0:
                return "That project has no tasks."
            if outcome.task_titles:
                titles = ", ".join(_quoted(t) for t in outcome.task_titles)
                n = len(outcome.task_titles)
                return f"You have {n} active task(s): {titles}."
            if outcome.latest_completed_task_title:
                return (
                    "You don't have any active tasks right now. Your latest "
                    f"completed task was {_quoted(outcome.latest_completed_task_title)}."
                )
            if outcome.task_scope == "all" or outcome.task_active == 0:
                return "You don't have any active tasks right now."
            return (
                f"That project has {outcome.task_total} task(s), "
                f"{outcome.task_active} active."
            )

        if outcome.kind == "task_status":
            if not outcome.task_title:
                return "I found that task, but I can't describe it yet."
            if outcome.task_status == "active":
                return f"'{outcome.task_title}' is still active."
            return f"'{outcome.task_title}' is {outcome.task_status}."

        if outcome.kind == "task_created":
            if outcome.project_name:
                return (
                    f"Created {_quoted(outcome.task_title)} "
                    f"in {outcome.project_name}."
                )
            return f"Created {_quoted(outcome.task_title)}."

        if outcome.kind == "task_updated":
            return (
                f"Done -- I marked '{outcome.task_title}' "
                f"{outcome.task_status}."
            )

        if outcome.kind == "activity_recall":
            if not outcome.recall_facts:
                if outcome.recall_window == "yesterday":
                    return "I don't have any activity from yesterday recorded."
                return "I don't have any recent activity recorded."
            parts = [_recall_phrase(f_) for f_ in outcome.recall_facts]
            prefix = (
                "Yesterday"
                if outcome.recall_window == "yesterday"
                else "Recently"
            )
            return prefix + ": " + ", ".join(parts) + "."

        if outcome.kind == "reminder_created":
            assert outcome.reminder_due_at is not None
            assert outcome.reminder_timezone is not None
            due = _friendly_due(
                outcome.reminder_due_at, outcome.reminder_timezone
            )
            return f"I'll remind you to {outcome.reminder_title} {due}."

        if outcome.kind == "reminder_list":
            if not outcome.reminders:
                return "You don't have any open reminders."
            rendered = [
                f"{_quoted(title)} {_friendly_due(due_at, timezone_name)}"
                for title, due_at, timezone_name, _status in outcome.reminders
            ]
            return "Your open reminders are: " + "; ".join(rendered) + "."

        if outcome.kind == "reminder_updated":
            verb = "completed" if outcome.reminder_status == "completed" else "cancelled"
            return f"Done -- I {verb} the reminder {_quoted(outcome.reminder_title)}."

        if outcome.kind == "notification_list":
            if not outcome.notifications:
                return "You don't have any active notifications."
            rendered = [
                f"{_quoted(title)}: {body}"
                for title, body, _status in outcome.notifications
            ]
            return "Your notifications are: " + "; ".join(rendered) + "."

        if outcome.kind == "notification_updated":
            verb = "marked as read" if outcome.notification_status == "read" else "dismissed"
            return f"I {verb} {_quoted(outcome.notification_title)}."

        if outcome.kind == "note_created":
            return f"I created the note {_quoted(outcome.note_title)}."

        if outcome.kind == "note_list":
            if not outcome.notes:
                return f"You don't have any {outcome.note_status} notes."
            titles = ", ".join(
                _quoted(title) for title, _content in outcome.notes
            )
            return f"Your {outcome.note_status} notes are: {titles}."

        if outcome.kind == "note_updated":
            if outcome.note_status == "archived":
                return f"I archived the note {_quoted(outcome.note_title)}."
            return f"I updated the note {_quoted(outcome.note_title)}."

        if outcome.kind == "list_created":
            return f"I created the {_quoted(outcome.list_title)} list."
        if outcome.kind == "list_list":
            if not outcome.list_titles:
                return "You don't have any active lists."
            return "Your active lists are: " + ", ".join(
                _quoted(title) for title in outcome.list_titles
            ) + "."
        if outcome.kind == "list_item_added":
            return f"I added {_quoted(outcome.list_item_content)} to the {_quoted(outcome.list_title)} list."
        if outcome.kind == "list_item_completed":
            return f"I marked {_quoted(outcome.list_item_content)} complete on the {_quoted(outcome.list_title)} list."
        if outcome.kind == "list_archived":
            return f"I archived the {_quoted(outcome.list_title)} list."

        if outcome.kind == "conversation":
            return outcome.reply or "Yes. I'm here."

        if outcome.kind == "no_match":
            return "I'm not sure what you want me to do."

        if outcome.kind == "target_not_found":
            if outcome.target_type == "project":
                return f"I couldn't find a project called {_quoted(outcome.target)}."
            if outcome.target_type == "list":
                return f"I couldn't find that active list or list item: {_quoted(outcome.target)}."
            if outcome.target_type == "note":
                return (
                    "I couldn't find an active note called "
                    f"{_quoted(outcome.target)}."
                )
            if outcome.target_type == "notification":
                return (
                    "I couldn't find an active notification called "
                    f"{_quoted(outcome.target)}."
                )
            if outcome.target_type == "reminder":
                return (
                    "I understood which reminder action you want, but I "
                    f"couldn't find an open reminder called {_quoted(outcome.target)}."
                )
            if outcome.target:
                return (
                    "I understood that you want to finish a task, but I "
                    f"couldn't find an active task called '{outcome.target}'."
                )
            return (
                "I understood that you want to finish a task, but I couldn't "
                "find an active task to complete."
            )

        if outcome.kind == "ambiguous":
            if outcome.reply:
                return outcome.reply
            listed = "\n".join(f"- {c}" for c in outcome.candidates)
            return f"I found several matches:\n{listed}\nWhich one?"

        if outcome.kind == "unsupported":
            return (
                outcome.reply
                or "I can't do that yet."
            )

        # Every OutcomeKind above is handled; reaching here is a bug.
        raise ValueError(f"unrenderable outcome kind: {outcome.kind}")


def _friendly_due(value: datetime, timezone_name: str) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = utc_to_local(value, timezone_name)
    return local.strftime("on %b %-d at %-I:%M %p")
