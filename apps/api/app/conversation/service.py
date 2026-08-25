"""ConversationService: the orchestrator and the trust boundary.

Flow for one turn:

    build WorldView (via real domain services, scoped to current_user)
        -> resolver.resolve(message, world)   [proposes only]
        -> validate action against closed registry   [refuse if unknown]
        -> validate arguments for that action
        -> dispatch into the authoritative domain service on the SHARED session
        -> build a truthful reply from the service's actual returned object

The domain services own mutation, transaction, and Activity emission. Because
ConversationService constructs them all on one AsyncSession, a mutation and
its Activity event commit atomically, exactly as they do through the HTTP
routes. The resolver never executes; the service is the only place that
validates and dispatches.

Honest non-execution: a no-match or ambiguous reference returns
``executed=False`` with an explanatory reply and mutates nothing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User

from app.projects.service import ProjectsService
from app.projects.schemas import ProjectCreate
from app.tasks.service import TasksService
from app.tasks.schemas import TaskCreate, TaskUpdate
from app.activity.service import ActivityService
from app.core.time import (
    Clock,
    TimeError,
    ensure_utc,
    resolve_timezone,
    system_clock,
)
from app.reminders.interpretation import interpret_reminder_time
from app.reminders.schemas import ReminderCreate
from app.reminders.service import RemindersService
from app.notifications.service import NotificationsService
from app.notes.schemas import NoteCreate, NoteUpdate
from app.notes.service import NotesService
from app.lists.schemas import ListCreate, ListItemCreate, ListItemUpdate, ListUpdate
from app.lists.service import ListsService

from app.conversation import registry
from app.conversation.context import (
    ConversationContextStore,
    GroundedProjectReference,
    GroundedTaskReference,
)
from app.conversation.exceptions import (
    AmbiguousReferenceError,
    CompletionTargetNotFoundError,
    NoMatchError,
    UnknownActionError,
)
from app.conversation.resolver import (
    HardcodedResolver,
    ProjectRef,
    NotificationRef,
    NoteRef,
    ListItemRef,
    ListRef,
    ReminderRef,
    Resolver,
    TaskRef,
    WorldView,
)
from app.conversation.schemas import ConversationResponse, ResolvedAction
from app.conversation.responder import (
    Outcome,
    RecallFact,
    Responder,
    TemplateResponder,
)
from app.conversation.understanding import (
    Clarification,
    ConversationTurn,
    UnderstandingProvider,
    UnderstandingProviderError,
    UnderstandingResult,
    Unsupported,
    ActionProposal,
)

logger = logging.getLogger(__name__)


def _target_type_for_action(
    action: str,
) -> str:
    if action in {registry.PROJECT_LIST, registry.PROJECT_CREATE}:
        return "project"
    if action in {registry.TASK_LIST, registry.TASK_CREATE, registry.TASK_UPDATE}:
        return "task"
    if action in {registry.REMINDER_COMPLETE, registry.REMINDER_CANCEL}:
        return "reminder"
    if action in {registry.NOTIFICATION_READ, registry.NOTIFICATION_DISMISS}:
        return "notification"
    if action in {registry.NOTE_CREATE, registry.NOTE_UPDATE, registry.NOTE_ARCHIVE}:
        return "note"
    if action in {
        registry.LIST_CREATE,
        registry.LIST_ADD_ITEM,
        registry.LIST_COMPLETE_ITEM,
        registry.LIST_ARCHIVE,
    }:
        return "list"
    return "task"


class ConversationService:
    """Orchestrates one conversational turn over the peer capabilities."""

    def __init__(
        self,
        session: AsyncSession,
        resolver: Resolver | None = None,
        responder: Responder | None = None,
        understanding_provider: UnderstandingProvider | None = None,
        context_store: ConversationContextStore | None = None,
        clock: Clock = system_clock,
    ) -> None:
        # One shared session across every composed service, so a dispatched
        # mutation and its Activity event commit atomically.
        self._session = session
        self._projects = ProjectsService(session)
        self._tasks = TasksService(session)
        self._activity = ActivityService(session)
        self._clock = clock
        self._reminders = RemindersService(session, clock=clock)
        self._notifications = NotificationsService(session, clock=clock)
        self._notes = NotesService(session, clock=clock)
        self._lists = ListsService(session, clock=clock)
        # Resolver is injectable so tests can pin behavior; defaults to the
        # deterministic v1 brain.
        self._resolver: Resolver = resolver or HardcodedResolver()
        # Responder renders outcomes into words; injectable so a later
        # LlmResponder can swap in behind the Protocol without touching
        # dispatch. Defaults to the deterministic grounded template.
        self._responder: Responder = responder or TemplateResponder()
        self._understanding_provider = understanding_provider
        self._context_store = context_store

    async def _build_world(self, current_user: User) -> WorldView:
        projects = await self._projects.list_projects(current_user)
        project_refs = tuple(
            ProjectRef(project_id=p.id, name=p.name) for p in projects
        )
        task_refs: list[TaskRef] = []
        for p in projects:
            tasks = await self._tasks.list_tasks(current_user, p.id)
            for t in tasks:
                task_refs.append(
                    TaskRef(
                        task_id=t.id,
                        project_id=p.id,
                        title=t.title,
                        project_name=p.name,
                        status=t.status,
                    )
                )
        reminders = await self._reminders.list_reminders(current_user)
        reminder_refs = tuple(
            ReminderRef(
                reminder_id=reminder.id,
                title=reminder.title,
                status=reminder.status,
                due_at=reminder.due_at,
                timezone=reminder.timezone,
            )
            for reminder in reminders
        )
        notifications = await self._notifications.list_notifications(current_user)
        notification_refs = tuple(
            NotificationRef(
                notification_id=item.id,
                title=item.title,
                body=item.body,
                status=item.status,
                created_at=item.created_at,
            )
            for item in notifications
        )
        active_notes = await self._notes.list_notes(current_user, status="active")
        archived_notes = await self._notes.list_notes(
            current_user, status="archived"
        )
        note_refs = tuple(
            NoteRef(
                note_id=note.id,
                title=note.title,
                content=note.content,
                status=note.status,
            )
            for note in (*active_notes, *archived_notes)
        )
        active_lists = await self._lists.list_lists(current_user, status="active")
        archived_lists = await self._lists.list_lists(current_user, status="archived")
        list_refs: list[ListRef] = []
        for value in (*active_lists, *archived_lists):
            items = await self._lists.list_items(current_user, value.id)
            list_refs.append(
                ListRef(
                    list_id=value.id, title=value.title, status=value.status,
                    items=tuple(
                        ListItemRef(item_id=item.id, content=item.content, status=item.status)
                        for item in items
                    ),
                )
            )
        return WorldView(
            projects=project_refs,
            tasks=tuple(task_refs),
            reminders=reminder_refs,
            notifications=notification_refs,
            notes=note_refs,
            lists=tuple(list_refs),
        )

    async def handle(
        self,
        current_user: User,
        message: str,
        timezone_name: str | None = None,
    ) -> ConversationResponse:
        world_started = time.perf_counter()
        world = await self._build_world(current_user)
        logger.info(
            "Conversation world build complete: elapsed_ms=%.1f",
            (time.perf_counter() - world_started) * 1000,
        )

        # Resolver proposes; it executes nothing. Deterministic handling stays
        # first so obvious/offline cases never depend on a model provider.
        try:
            action = self._resolver.resolve(message, world)
        except NoMatchError:
            outcome = await self._try_provider(
                current_user,
                message,
                world,
                timezone_name,
                fallback=Outcome(kind="no_match", executed=False),
            )
            return self._respond(outcome)
        except CompletionTargetNotFoundError as exc:
            outcome = await self._try_provider(
                current_user,
                message,
                world,
                timezone_name,
                fallback=Outcome(
                    kind="target_not_found",
                    executed=False,
                    target=exc.target,
                    target_type=(
                        "reminder"
                        if "reminder" in message.lower()
                        else (
                            "notification"
                            if "notification" in message.lower()
                            else (
                                "note" if "note" in message.lower() else "task"
                                if "list" not in message.lower()
                                else "list"
                            )
                        )
                    ),
                ),
            )
            return self._respond(outcome)
        except AmbiguousReferenceError as exc:
            return self._respond(
                Outcome(
                    kind="ambiguous",
                    executed=False,
                    candidates=tuple(exc.candidates),
                )
            )

        # Trust boundary: the proposed action must be in the closed registry.
        if not registry.is_allowed(action.action):
            raise UnknownActionError(action.action)

        outcome = await self._dispatch(
            current_user, action, world, timezone_name
        )
        self._remember_outcome(current_user, outcome)
        return self._respond(outcome)

    async def _try_provider(
        self,
        current_user: User,
        message: str,
        world: WorldView,
        timezone_name: str | None,
        fallback: Outcome,
    ) -> Outcome:
        if self._understanding_provider is None:
            return fallback

        self._current_context_task = (
            self._context_store.get_last_task(current_user.id)
            if self._context_store is not None
            else None
        )
        try:
            provider_started = time.perf_counter()
            result = await self._understanding_provider.understand(
                message=message,
                world=world,
                context=self._context_payload(current_user),
            )
        except UnderstandingProviderError as exc:
            logger.warning(
                "Conversation understanding provider failed: elapsed_ms=%.1f",
                (time.perf_counter() - provider_started) * 1000,
                extra={"provider_error_code": exc.code},
            )
            return fallback
        logger.info(
            "Conversation understanding provider complete: elapsed_ms=%.1f",
            (time.perf_counter() - provider_started) * 1000,
        )

        outcome = await self._outcome_from_understanding(
            current_user, result, world, timezone_name
        )
        self._remember_outcome(current_user, outcome)
        return outcome

    async def _outcome_from_understanding(
        self,
        current_user: User,
        result: UnderstandingResult,
        world: WorldView,
        timezone_name: str | None,
    ) -> Outcome:
        if isinstance(result, ConversationTurn):
            if result.reference:
                try:
                    task = self._resolve_task_reference(
                        result.reference, world, require_active=False
                    )
                except CompletionTargetNotFoundError:
                    return Outcome(
                        kind="conversation",
                        executed=False,
                        reply=result.reply or "I couldn't find that task.",
                    )
                except AmbiguousReferenceError as exc:
                    return Outcome(
                        kind="ambiguous",
                        executed=False,
                        candidates=tuple(exc.candidates),
                    )
                return Outcome(
                    kind="task_status",
                    executed=False,
                    task_title=task.title,
                    task_status=task.status,
                    project_name=task.project_name,
                )
            return Outcome(
                kind="conversation",
                executed=False,
                reply=result.reply or "Yes. I can hear you.",
            )

        if isinstance(result, Clarification):
            return Outcome(
                kind="ambiguous",
                executed=False,
                candidates=result.candidates,
            )

        if isinstance(result, Unsupported):
            return Outcome(
                kind="unsupported",
                executed=False,
                reply=result.reason or "I can't do that yet.",
            )

        if not isinstance(result, ActionProposal):
            return Outcome(kind="no_match", executed=False)

        proposal = result
        if not registry.is_allowed(proposal.action):
            return Outcome(
                kind="unsupported",
                executed=False,
                reply="I can't do that yet.",
            )

        try:
            action = self._resolved_action_from_proposal(proposal, world)
        except CompletionTargetNotFoundError as exc:
            return Outcome(
                kind="target_not_found",
                executed=False,
                target=exc.target,
                target_type=_target_type_for_action(result.action),
            )
        except AmbiguousReferenceError as exc:
            return Outcome(
                kind="ambiguous",
                executed=False,
                candidates=tuple(exc.candidates),
            )

        return await self._dispatch(
            current_user, action, world, timezone_name
        )

    def _respond(self, outcome: Outcome) -> ConversationResponse:
        """Render an Outcome into the wire response. The reply string is
        the Responder's job; the service only carries executed/action."""
        return ConversationResponse(
            executed=outcome.executed,
            action=outcome.action,
            reply=self._responder.render(outcome),
        )

    # How far back "recently" reaches, and the most events recall will
    # narrate. Both are fixed in v1: the deterministic resolver proposes no
    # window, so the dispatch owns it. A later LlmResolver may propose a
    # window; that is when these stop being constants.
    _RECALL_WINDOW = timedelta(hours=24)
    _RECALL_MAX = 10

    async def _dispatch(
        self,
        current_user: User,
        action: ResolvedAction,
        world: WorldView,
        timezone_name: str | None = None,
    ) -> Outcome:
        if action.action == registry.PROJECT_LIST:
            projects = await self._projects.list_projects(current_user)
            return Outcome(
                kind="project_list",
                executed=True,
                action=action.action,
                project_names=tuple(p.name for p in projects),
            )

        if action.action == registry.PROJECT_CREATE:
            assert action.project_name is not None
            project = await self._projects.create_project(
                current_user,
                ProjectCreate(name=action.project_name),
            )
            return Outcome(
                kind="project_created",
                executed=True,
                action=action.action,
                project_name=project.name,
            )

        if action.action == registry.TASK_CREATE:
            assert action.task_title is not None
            project_id = action.project_id
            project_name = action.project_name
            if project_id is None:
                try:
                    project = self._resolve_context_project(
                        current_user, world
                    )
                except CompletionTargetNotFoundError as exc:
                    return Outcome(
                        kind="target_not_found",
                        executed=False,
                        target=exc.target,
                        target_type="project",
                    )
                except AmbiguousReferenceError as exc:
                    return Outcome(
                        kind="ambiguous",
                        executed=False,
                        candidates=tuple(exc.candidates),
                    )
                project_id = project.project_id
                project_name = project.name
            task = await self._tasks.create_task(
                current_user,
                project_id,
                TaskCreate(title=action.task_title),
            )
            return Outcome(
                kind="task_created",
                executed=True,
                action=action.action,
                task_title=task.title,
                project_name=project_name,
            )

        if action.action == registry.TASK_LIST:
            if action.project_id is None:
                active_refs = [
                    t for t in world.tasks if t.status == "active"
                ]
                return Outcome(
                    kind="task_list",
                    executed=True,
                    action=action.action,
                    task_scope="all",
                    task_total=len(world.tasks),
                    task_active=len(active_refs),
                    task_titles=tuple(t.title for t in active_refs[:5]),
                    latest_completed_task_title=(
                        await self._latest_completed_task_title(
                            current_user, world
                        )
                    ),
                )

            tasks = await self._tasks.list_tasks(
                current_user, action.project_id
            )
            active = [t for t in tasks if t.status == "active"]
            return Outcome(
                kind="task_list",
                executed=True,
                action=action.action,
                task_scope="project",
                task_total=len(tasks),
                task_active=len(active),
                task_titles=tuple(t.title for t in active[:5]),
            )

        if action.action == registry.TASK_UPDATE:
            assert action.project_id is not None
            assert action.task_id is not None
            task = await self._tasks.update_task(
                current_user,
                action.project_id,
                action.task_id,
                TaskUpdate(status=action.status),
            )
            return Outcome(
                kind="task_updated",
                executed=True,
                action=action.action,
                task_title=task.title,
                task_status=task.status,
                project_name=next(
                    (
                        t.project_name
                        for t in world.tasks
                        if t.task_id == task.id
                    ),
                    None,
                ),
            )

        if action.action == registry.ACTIVITY_RECALL:
            activities = await self._activity.list_activities(current_user)
            if action.recall_window == "yesterday":
                selected = self._window_yesterday(activities, timezone_name)
                recall_window = "yesterday"
            else:
                selected = self._window_recent(activities)
                recall_window = "recent"
            facts = self._recall_facts(selected, world)
            return Outcome(
                kind="activity_recall",
                executed=True,
                action=action.action,
                recall_facts=facts,
                recall_window=recall_window,
            )

        if action.action == registry.REMINDER_CREATE:
            assert action.reminder_title is not None
            assert action.reminder_when is not None
            try:
                timezone_key = resolve_timezone(
                    timezone_name or current_user.timezone
                ).key
                due_at = interpret_reminder_time(
                    action.reminder_when, timezone_key, self._clock
                )
            except (TimeError, ValueError) as exc:
                return Outcome(
                    kind="unsupported",
                    executed=False,
                    reply=str(exc),
                )
            reminder = await self._reminders.create_reminder(
                current_user,
                ReminderCreate(
                    title=action.reminder_title,
                    due_at=due_at,
                    timezone=timezone_key,
                ),
            )
            return Outcome(
                kind="reminder_created",
                executed=True,
                action=action.action,
                reminder_title=reminder.title,
                reminder_due_at=reminder.due_at,
                reminder_timezone=reminder.timezone,
            )

        if action.action == registry.REMINDER_LIST:
            reminders = await self._reminders.list_reminders(current_user)
            open_reminders = [
                reminder
                for reminder in reminders
                if reminder.status in {"scheduled", "due"}
            ]
            return Outcome(
                kind="reminder_list",
                executed=True,
                action=action.action,
                reminders=tuple(
                    (
                        reminder.title,
                        reminder.due_at,
                        reminder.timezone,
                        reminder.status,
                    )
                    for reminder in open_reminders
                ),
            )

        if action.action in {
            registry.REMINDER_COMPLETE,
            registry.REMINDER_CANCEL,
        }:
            assert action.reminder_id is not None
            if action.action == registry.REMINDER_COMPLETE:
                reminder = await self._reminders.complete_reminder(
                    current_user, action.reminder_id
                )
            else:
                reminder = await self._reminders.cancel_reminder(
                    current_user, action.reminder_id
                )
            return Outcome(
                kind="reminder_updated",
                executed=True,
                action=action.action,
                reminder_title=reminder.title,
                reminder_status=reminder.status,
            )

        if action.action == registry.NOTIFICATION_LIST:
            notifications = await self._notifications.list_notifications(
                current_user, status=action.notification_status
            )
            visible = [item for item in notifications if item.status != "dismissed"]
            return Outcome(
                kind="notification_list",
                executed=True,
                action=action.action,
                notifications=tuple(
                    (item.title, item.body, item.status) for item in visible
                ),
            )

        if action.action in {
            registry.NOTIFICATION_READ,
            registry.NOTIFICATION_DISMISS,
        }:
            assert action.notification_id is not None
            if action.action == registry.NOTIFICATION_READ:
                notification = await self._notifications.mark_read(
                    current_user, action.notification_id
                )
            else:
                notification = await self._notifications.dismiss(
                    current_user, action.notification_id
                )
            return Outcome(
                kind="notification_updated",
                executed=True,
                action=action.action,
                notification_title=notification.title,
                notification_status=notification.status,
            )

        if action.action == registry.NOTE_CREATE:
            assert action.note_title is not None
            note = await self._notes.create_note(
                current_user,
                NoteCreate(
                    title=action.note_title,
                    content=action.note_content or "",
                ),
            )
            return Outcome(
                kind="note_created",
                executed=True,
                action=action.action,
                note_title=note.title,
            )

        if action.action == registry.NOTE_LIST:
            note_status = action.note_status or "active"
            notes = await self._notes.list_notes(
                current_user, status=note_status
            )
            return Outcome(
                kind="note_list",
                executed=True,
                action=action.action,
                note_status=note_status,
                notes=tuple((note.title, note.content) for note in notes),
            )

        if action.action in {registry.NOTE_UPDATE, registry.NOTE_ARCHIVE}:
            assert action.note_id is not None
            data = (
                NoteUpdate(status="archived")
                if action.action == registry.NOTE_ARCHIVE
                else NoteUpdate.model_validate(
                    {
                        key: value
                        for key, value in {
                            "title": action.note_title,
                            "content": action.note_content,
                        }.items()
                        if value is not None
                    }
                )
            )
            note = await self._notes.update_note(
                current_user, action.note_id, data
            )
            return Outcome(
                kind="note_updated",
                executed=True,
                action=action.action,
                note_title=note.title,
                note_status=note.status,
            )

        if action.action == registry.LIST_CREATE:
            assert action.list_title is not None
            value = await self._lists.create_list(
                current_user, ListCreate(title=action.list_title)
            )
            return Outcome(kind="list_created", executed=True, action=action.action,
                           list_title=value.title)
        if action.action == registry.LIST_LIST:
            values = await self._lists.list_lists(current_user)
            return Outcome(kind="list_list", executed=True, action=action.action,
                           list_titles=tuple(value.title for value in values))
        if action.action == registry.LIST_ADD_ITEM:
            assert action.list_id is not None and action.list_item_content is not None
            item = await self._lists.create_item(
                current_user, action.list_id,
                ListItemCreate(content=action.list_item_content),
            )
            return Outcome(kind="list_item_added", executed=True, action=action.action,
                           list_title=action.list_title, list_item_content=item.content)
        if action.action == registry.LIST_COMPLETE_ITEM:
            assert action.list_id is not None and action.list_item_id is not None
            item = await self._lists.update_item(
                current_user, action.list_id, action.list_item_id,
                ListItemUpdate(status="complete"),
            )
            return Outcome(kind="list_item_completed", executed=True, action=action.action,
                           list_title=action.list_title, list_item_content=item.content)
        if action.action == registry.LIST_ARCHIVE:
            assert action.list_id is not None
            value = await self._lists.update_list(
                current_user, action.list_id, ListUpdate(status="archived")
            )
            return Outcome(kind="list_archived", executed=True, action=action.action,
                           list_title=value.title)

        # Registry membership was checked upstream; reaching here is a bug.
        raise UnknownActionError(action.action)

    def _resolved_action_from_proposal(
        self, proposal: ActionProposal, world: WorldView
    ) -> ResolvedAction:
        arguments = proposal.arguments or {}
        if (
            proposal.action != registry.ACTIVITY_RECALL
            and proposal.recall_window is not None
        ):
            raise CompletionTargetNotFoundError("that action")

        if proposal.action == registry.TASK_UPDATE:
            if set(arguments) != {"status"} or arguments.get("status") != "complete":
                raise CompletionTargetNotFoundError(
                    proposal.reference or "that task"
                )
            task = self._resolve_task_reference(
                proposal.reference, world, require_active=True
            )
            return ResolvedAction(
                action=registry.TASK_UPDATE,
                project_id=task.project_id,
                task_id=task.task_id,
                status="complete",
            )

        if proposal.action == registry.ACTIVITY_RECALL:
            if arguments or proposal.reference:
                raise CompletionTargetNotFoundError("activity recall")
            return ResolvedAction(
                action=registry.ACTIVITY_RECALL,
                recall_window=proposal.recall_window,
            )

        if proposal.action == registry.PROJECT_LIST:
            if arguments or proposal.reference:
                raise CompletionTargetNotFoundError("projects")
            return ResolvedAction(action=registry.PROJECT_LIST)

        if proposal.action == registry.PROJECT_CREATE:
            if proposal.reference or set(arguments) != {"name"}:
                raise CompletionTargetNotFoundError("that project")
            name = arguments.get("name")
            if not name:
                raise CompletionTargetNotFoundError("that project")
            return ResolvedAction(
                action=registry.PROJECT_CREATE,
                project_name=name,
            )

        if proposal.action == registry.TASK_CREATE:
            if set(arguments) != {"title"}:
                raise CompletionTargetNotFoundError("that task")
            title = arguments.get("title")
            if not title:
                raise CompletionTargetNotFoundError("that task")
            if proposal.reference:
                project = self._resolve_project_reference(
                    proposal.reference, world
                )
                return ResolvedAction(
                    action=registry.TASK_CREATE,
                    project_id=project.project_id,
                    project_name=project.name,
                    task_title=title,
                )
            return ResolvedAction(
                action=registry.TASK_CREATE,
                task_title=title,
            )

        if proposal.action == registry.TASK_LIST:
            if arguments:
                raise CompletionTargetNotFoundError("tasks")
            if not proposal.reference:
                return ResolvedAction(action=registry.TASK_LIST)
            project = self._resolve_project_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=registry.TASK_LIST,
                project_id=project.project_id,
            )

        if proposal.action == registry.REMINDER_CREATE:
            if proposal.reference or set(arguments) != {"title", "when"}:
                raise CompletionTargetNotFoundError("that reminder")
            title = arguments.get("title")
            when = arguments.get("when")
            if not title or not when:
                raise CompletionTargetNotFoundError("that reminder")
            return ResolvedAction(
                action=registry.REMINDER_CREATE,
                reminder_title=title,
                reminder_when=when,
            )

        if proposal.action == registry.REMINDER_LIST:
            if arguments or proposal.reference:
                raise CompletionTargetNotFoundError("reminders")
            return ResolvedAction(action=registry.REMINDER_LIST)

        if proposal.action in {
            registry.REMINDER_COMPLETE,
            registry.REMINDER_CANCEL,
        }:
            if arguments:
                raise CompletionTargetNotFoundError(
                    proposal.reference or "that reminder"
                )
            reminder = self._resolve_reminder_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=proposal.action,
                reminder_id=reminder.reminder_id,
                reminder_title=reminder.title,
            )

        if proposal.action == registry.NOTIFICATION_LIST:
            if proposal.reference or set(arguments) - {"status"}:
                raise CompletionTargetNotFoundError("notifications")
            status = arguments.get("status")
            if status not in {None, "unread"}:
                raise CompletionTargetNotFoundError("notifications")
            return ResolvedAction(
                action=registry.NOTIFICATION_LIST,
                notification_status="unread" if status == "unread" else None,
            )

        if proposal.action in {
            registry.NOTIFICATION_READ,
            registry.NOTIFICATION_DISMISS,
        }:
            if arguments:
                raise CompletionTargetNotFoundError(
                    proposal.reference or "that notification"
                )
            notification = self._resolve_notification_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=proposal.action,
                notification_id=notification.notification_id,
            )

        if proposal.action == registry.NOTE_CREATE:
            if proposal.reference or set(arguments) - {"title", "content"}:
                raise CompletionTargetNotFoundError("that note")
            title = arguments.get("title")
            if not title:
                raise CompletionTargetNotFoundError("that note")
            return ResolvedAction(
                action=registry.NOTE_CREATE,
                note_title=title,
                note_content=arguments.get("content", ""),
            )

        if proposal.action == registry.NOTE_LIST:
            if proposal.reference or set(arguments) - {"status"}:
                raise CompletionTargetNotFoundError("notes")
            status = arguments.get("status")
            if status not in {None, "active", "archived"}:
                raise CompletionTargetNotFoundError("notes")
            return ResolvedAction(
                action=registry.NOTE_LIST,
                note_status="archived" if status == "archived" else "active",
            )

        if proposal.action in {registry.NOTE_UPDATE, registry.NOTE_ARCHIVE}:
            note = self._resolve_note_reference(proposal.reference, world)
            allowed_arguments = (
                {"title", "content"}
                if proposal.action == registry.NOTE_UPDATE
                else set()
            )
            if set(arguments) - allowed_arguments:
                raise CompletionTargetNotFoundError(note.title)
            if proposal.action == registry.NOTE_UPDATE and not (
                arguments.get("content") or arguments.get("title")
            ):
                raise CompletionTargetNotFoundError(note.title)
            return ResolvedAction(
                action=proposal.action,
                note_id=note.note_id,
                note_title=arguments.get("title"),
                note_content=arguments.get("content"),
            )

        if proposal.action == registry.LIST_CREATE:
            if proposal.reference or set(arguments) != {"title"} or not arguments.get("title"):
                raise CompletionTargetNotFoundError("that list")
            return ResolvedAction(action=proposal.action, list_title=arguments["title"])
        if proposal.action == registry.LIST_LIST:
            if arguments or proposal.reference:
                raise CompletionTargetNotFoundError("lists")
            return ResolvedAction(action=proposal.action)
        if proposal.action in {registry.LIST_ADD_ITEM, registry.LIST_ARCHIVE}:
            value = self._resolve_list_reference(proposal.reference, world)
            if proposal.action == registry.LIST_ADD_ITEM:
                if set(arguments) != {"content"} or not arguments.get("content"):
                    raise CompletionTargetNotFoundError(value.title)
                content = arguments["content"]
            else:
                if arguments:
                    raise CompletionTargetNotFoundError(value.title)
                content = None
            return ResolvedAction(action=proposal.action, list_id=value.list_id,
                                  list_title=value.title, list_item_content=content)
        if proposal.action == registry.LIST_COMPLETE_ITEM:
            if set(arguments) != {"item"} or not arguments.get("item"):
                raise CompletionTargetNotFoundError("that list item")
            value = self._resolve_list_reference(proposal.reference, world)
            item = self._resolve_list_item_reference(arguments["item"], value)
            return ResolvedAction(action=proposal.action, list_id=value.list_id,
                                  list_title=value.title, list_item_id=item.item_id,
                                  list_item_content=item.content)

        raise UnknownActionError(proposal.action)

    _PRONOUN_REFS = frozenset(
        {"that", "it", "that one", "this", "this one", "that task"}
    )

    def _resolve_task_reference(
        self,
        reference: str | None,
        world: WorldView,
        *,
        require_active: bool,
    ) -> TaskRef:
        target = (reference or "").strip().lower()
        if target in self._PRONOUN_REFS:
            ctx = self._context_payload_user_task()
            if ctx is not None:
                target = ctx.title.lower()

        if not target:
            raise CompletionTargetNotFoundError("that task")

        matches = [
            t
            for t in world.tasks
            if (not require_active or t.status == "active")
            and (target in t.title.lower() or t.title.lower() in target)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError(
                [f"{m.title} ({m.project_name})" for m in matches]
            )
        raise CompletionTargetNotFoundError(target)

    def _resolve_project_reference(
        self, reference: str | None, world: WorldView
    ) -> ProjectRef:
        target = (reference or "").strip().lower()
        exact = [p for p in world.projects if p.name.lower() == target]
        if len(exact) == 1:
            return exact[0]
        matches = [
            p
            for p in world.projects
            if target and (target in p.name.lower() or p.name.lower() in target)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([p.name for p in matches])
        raise CompletionTargetNotFoundError(target or "that project")

    def _resolve_context_project(
        self, current_user: User, world: WorldView
    ) -> ProjectRef:
        if self._context_store is None:
            raise CompletionTargetNotFoundError("a project")
        ref = self._context_store.get_last_project(current_user.id)
        if ref is None:
            raise CompletionTargetNotFoundError("a project")
        return self._resolve_project_reference(ref.name, world)

    def _resolve_reminder_reference(
        self, reference: str | None, world: WorldView
    ) -> ReminderRef:
        target = (reference or "").strip().lower()
        matches = [
            reminder
            for reminder in world.reminders
            if reminder.status in {"scheduled", "due"}
            and target
            and (
                target in reminder.title.lower()
                or reminder.title.lower() in target
            )
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([item.title for item in matches])
        raise CompletionTargetNotFoundError(target or "that reminder")

    def _resolve_notification_reference(
        self, reference: str | None, world: WorldView
    ) -> NotificationRef:
        target = (reference or "").strip().lower()
        candidates = [
            item for item in world.notifications if item.status != "dismissed"
        ]
        matches = [
            item
            for item in candidates
            if target
            and (target in item.title.lower() or item.title.lower() in target)
        ]
        if not target and len(candidates) == 1:
            return candidates[0]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 or (not target and len(candidates) > 1):
            raise AmbiguousReferenceError([item.title for item in candidates])
        raise CompletionTargetNotFoundError(target or "that notification")

    def _resolve_note_reference(
        self, reference: str | None, world: WorldView
    ) -> NoteRef:
        target = (reference or "").strip().lower()
        matches = [
            note
            for note in world.notes
            if note.status == "active"
            and target
            and (target in note.title.lower() or note.title.lower() in target)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([note.title for note in matches])
        raise CompletionTargetNotFoundError(target or "that note")

    def _resolve_list_reference(self, reference: str | None, world: WorldView) -> ListRef:
        target = (reference or "").strip().lower()
        matches = [value for value in world.lists if value.status == "active" and target
                   and (target in value.title.lower() or value.title.lower() in target)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([value.title for value in matches])
        raise CompletionTargetNotFoundError(target or "that list")

    def _resolve_list_item_reference(self, reference: str, value: ListRef) -> ListItemRef:
        target = reference.strip().lower()
        matches = [item for item in value.items if item.status == "active"
                   and (target in item.content.lower() or item.content.lower() in target)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([item.content for item in matches])
        raise CompletionTargetNotFoundError(target or "that list item")

    def _context_payload(self, current_user: User) -> dict | None:
        if self._context_store is None:
            return None
        task_ref = self._context_store.get_last_task(current_user.id)
        project_ref = self._context_store.get_last_project(current_user.id)
        if task_ref is None and project_ref is None:
            return None
        payload: dict = {}
        if task_ref is not None:
            payload["last_grounded_entity"] = {
                "kind": "task",
                "title": task_ref.title,
                "project": task_ref.project_name,
            }
        if project_ref is not None:
            payload["last_grounded_project"] = {
                "kind": "project",
                "name": project_ref.name,
            }
        return payload

    def _context_payload_user_task(self) -> GroundedTaskReference | None:
        # Set transiently by _outcome_from_understanding for the current turn.
        return getattr(self, "_current_context_task", None)

    def _remember_outcome(self, current_user: User, outcome: Outcome) -> None:
        if self._context_store is None:
            return
        if outcome.task_title and outcome.project_name:
            self._context_store.set_last_task(
                current_user.id,
                GroundedTaskReference(
                    title=outcome.task_title,
                    project_name=outcome.project_name,
                ),
            )
        if outcome.project_name:
            self._context_store.set_last_project(
                current_user.id,
                GroundedProjectReference(name=outcome.project_name),
            )

    def _window_recent(
        self, activities: list[Activity]
    ) -> list[Activity]:
        """Most-recent-first, within the recall window, capped. Coerces naive
        timestamps to UTC so the SQLite harness (which may return naive
        datetimes) compares cleanly against an aware cutoff."""
        cutoff = ensure_utc(self._clock.now()) - self._RECALL_WINDOW

        def _aware(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        within = [
            a
            for a in activities
            if a.created_at is not None and _aware(a.created_at) >= cutoff
        ]
        within.sort(key=lambda a: _aware(a.created_at), reverse=True)
        return within[: self._RECALL_MAX]

    def _window_yesterday(
        self, activities: list[Activity], timezone_name: str | None
    ) -> list[Activity]:
        """Return activity from the user's local calendar yesterday.

        The browser may send an invalid or missing IANA timezone. v0.1 uses
        UTC as the explicit fallback so the result stays deterministic instead
        of guessing a location.
        """
        try:
            user_tz = (
                ZoneInfo(timezone_name)
                if timezone_name
                else timezone.utc
            )
        except ZoneInfoNotFoundError:
            user_tz = timezone.utc

        now_local = ensure_utc(self._clock.now()).astimezone(user_tz)
        yesterday = now_local.date() - timedelta(days=1)
        start_local = datetime.combine(
            yesterday, datetime.min.time(), tzinfo=user_tz
        )
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        def _aware_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        within = [
            a
            for a in activities
            if a.created_at is not None
            and start_utc <= _aware_utc(a.created_at) < end_utc
        ]
        within.sort(key=lambda a: _aware_utc(a.created_at), reverse=True)
        return within[: self._RECALL_MAX]

    def _recall_facts(
        self, activities: list[Activity], world: WorldView
    ) -> tuple[RecallFact, ...]:
        return tuple(
            RecallFact(
                event_type=a.event_type,
                entity_type=a.entity_type,
                entity_label=self._entity_label(a, world),
            )
            for a in activities
        )

    async def _latest_completed_task_title(
        self, current_user: User, world: WorldView
    ) -> str | None:
        activities = await self._activity.list_activities(current_user)
        for activity in activities:
            if (
                activity.event_type == "task.completed"
                and activity.entity_type == "task"
            ):
                return self._entity_label(activity, world)
        return None

    def _entity_label(
        self, activity: Activity, world: WorldView
    ) -> str | None:
        """A human label for the entity an event concerned. Prefers a title
        carried in the payload, then resolves against the WorldView. If the
        entity is no longer resolvable, returns None so the responder can use
        a neutral phrase without leaking storage identifiers."""
        payload = activity.payload or {}
        for key in ("title", "task_title", "name", "content", "list_title"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        if activity.entity_type == "task":
            for task in world.tasks:
                if task.task_id == activity.entity_id:
                    return task.title
        if activity.entity_type == "project":
            for project in world.projects:
                if project.project_id == activity.entity_id:
                    return project.name
        if activity.entity_type == "note":
            for note in world.notes:
                if note.note_id == activity.entity_id:
                    return note.title
        if activity.entity_type == "list":
            for value in world.lists:
                if value.list_id == activity.entity_id:
                    return value.title
        if activity.entity_type == "list_item":
            for value in world.lists:
                for item in value.items:
                    if item.item_id == activity.entity_id:
                        return item.content
        return None
