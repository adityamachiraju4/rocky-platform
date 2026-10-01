"""Rocky-owned lexical retrieval over authoritative owned domain services."""
from __future__ import annotations

from dataclasses import replace
import logging
import re
import time
import unicodedata

from pydantic import ValidationError

from app.conversation.resolver import (
    ListItemRef, ListRef, NoteRef, NotificationRef, ProjectRef, ReminderRef,
    TaskRef, WorldView,
)
from app.conversation.understanding import PersonalContextRequest, UnderstandingProviderError
from app.core.time import UTC
from app.lists.service import ListsService
from app.models.user import User
from app.notes.service import NotesService
from app.notifications.service import NotificationsService
from app.projects.service import ProjectsService
from app.reminders.service import RemindersService
from app.tasks.service import TasksService

logger = logging.getLogger(__name__)
MAX_PER_SCOPE = 8
MAX_TOTAL_RECORDS = 48
MAX_LIST_ITEMS = 8
MAX_TITLE_CHARS = 200
MAX_CONTENT_CHARS = 1000


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def _score(query: str, title: str, body: str = "") -> tuple[int, int, int, int]:
    if query == "*":
        return (1, 0, 0, 0)
    subject, heading, content = map(_normalize, (query, title, body))
    tokens = set(subject.split())
    if not tokens:
        return (0, 0, 0, 0)
    return (
        int(subject == heading),
        int(f" {subject} " in f" {heading} "),
        len(tokens & set(heading.split())),
        len(tokens & set(content.split())),
    )


def _timestamp(value) -> float:
    return value.replace(tzinfo=UTC).timestamp() if value.tzinfo is None else value.timestamp()


class PersonalContextRetriever:
    def __init__(
        self, *, projects: ProjectsService, tasks: TasksService,
        reminders: RemindersService, notifications: NotificationsService,
        notes: NotesService, lists: ListsService,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._reminders = reminders
        self._notifications = notifications
        self._notes = notes
        self._lists = lists

    @staticmethod
    def _select(records, query, title, body, identity, tie=lambda value: ()):
        ranked = [(value, _score(query, title(value), body(value))) for value in records]
        ranked = [(value, score) for value, score in ranked if any(score)]
        ranked.sort(key=lambda pair: (
            tuple(-part for part in pair[1]), tie(pair[0]), str(identity(pair[0])),
        ))
        return [value for value, _ in ranked[:MAX_PER_SCOPE]]

    async def retrieve(self, current_user: User, request: PersonalContextRequest) -> WorldView:
        started = time.perf_counter()
        try:
            validated = request.validated()
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning("personal_context_retrieval_failed", extra={"failure_class": "invalid_request"})
            raise UnderstandingProviderError("Malformed personal retrieval request.") from exc
        query = validated.query
        scopes = set(validated.scopes)
        selected = {scope: () for scope in (
            "projects", "tasks", "reminders", "notifications", "notes", "lists"
        )}
        # Tasks need owned project IDs for enumeration, but those projects are
        # not placed in the model world unless projects was requested.
        projects = await self._projects.list_projects(current_user) if scopes & {"projects", "tasks"} else []
        if "projects" in scopes:
            values = self._select(projects, query, lambda p: p.name, lambda p: "", lambda p: p.id)
            selected["projects"] = tuple(ProjectRef(p.id, p.name[:MAX_TITLE_CHARS]) for p in values)
        if "tasks" in scopes:
            records = []
            for project in projects:
                tasks = await self._tasks.list_tasks(current_user, project.id)
                records.extend(TaskRef(t.id, project.id, t.title, project.name, t.status) for t in tasks)
            values = self._select(records, query, lambda t: t.title, lambda t: t.project_name,
                                  lambda t: t.task_id, lambda t: (t.status != "active",))
            selected["tasks"] = tuple(replace(t, title=t.title[:MAX_TITLE_CHARS], project_name=t.project_name[:MAX_TITLE_CHARS]) for t in values)
        if "reminders" in scopes:
            records = await self._reminders.list_reminders(current_user)
            values = self._select(records, query, lambda r: r.title, lambda r: "", lambda r: r.id,
                                  lambda r: (r.status not in {"due", "scheduled"}, _timestamp(r.due_at)))
            selected["reminders"] = tuple(ReminderRef(r.id, r.title[:MAX_TITLE_CHARS], r.status, r.due_at, r.timezone[:100]) for r in values)
        if "notifications" in scopes:
            records = await self._notifications.list_notifications(current_user)
            values = self._select(records, query, lambda n: n.title, lambda n: n.body, lambda n: n.id,
                                  lambda n: (n.status != "unread", -_timestamp(n.created_at)))
            selected["notifications"] = tuple(NotificationRef(n.id, n.title[:MAX_TITLE_CHARS], n.body[:MAX_CONTENT_CHARS], n.status, n.created_at) for n in values)
        if "notes" in scopes:
            records = []
            for status in ("active", "archived"):
                records.extend(await self._notes.list_notes(current_user, status=status))
            values = self._select(records, query, lambda n: n.title, lambda n: n.content, lambda n: n.id,
                                  lambda n: (n.status != "active",))
            selected["notes"] = tuple(NoteRef(n.id, n.title[:MAX_TITLE_CHARS], n.content[:MAX_CONTENT_CHARS], n.status) for n in values)
        if "lists" in scopes:
            records = []
            for status in ("active", "archived"):
                for value in await self._lists.list_lists(current_user, status=status):
                    items = await self._lists.list_items(current_user, value.id)
                    records.append(ListRef(value.id, value.title, value.status,
                                           tuple(ListItemRef(i.id, i.content, i.status) for i in items)))
            values = self._select(records, query, lambda v: v.title,
                                  lambda v: " ".join(i.content for i in v.items), lambda v: v.list_id,
                                  lambda v: (v.status != "active",))
            bounded = []
            for value in values:
                items = sorted(value.items, key=lambda i: (
                    tuple(-part for part in _score(query, i.content)), i.status != "active", str(i.item_id),
                ))[:MAX_LIST_ITEMS]
                bounded.append(replace(value, title=value.title[:MAX_TITLE_CHARS], items=tuple(
                    replace(i, content=i.content[:MAX_CONTENT_CHARS]) for i in items
                )))
            selected["lists"] = tuple(bounded)
        remaining = MAX_TOTAL_RECORDS
        for scope in selected:
            selected[scope] = selected[scope][:remaining]
            remaining -= len(selected[scope])
        logger.info("personal_context_retrieved", extra={
            "requested_scopes": sorted(scopes),
            "selected_counts": {scope: len(values) for scope, values in selected.items()},
            "retrieval_duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "query_specific": query != "*",
        })
        return WorldView(**selected)
