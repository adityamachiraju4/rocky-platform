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

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.activity import Activity

from app.projects.service import ProjectsService
from app.tasks.service import TasksService
from app.tasks.schemas import TaskUpdate
from app.activity.service import ActivityService

from app.conversation import registry
from app.conversation.exceptions import (
    AmbiguousReferenceError,
    CompletionTargetNotFoundError,
    NoMatchError,
    UnknownActionError,
)
from app.conversation.resolver import (
    HardcodedResolver,
    ProjectRef,
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


class ConversationService:
    """Orchestrates one conversational turn over the peer capabilities."""

    def __init__(
        self,
        session: AsyncSession,
        resolver: Resolver | None = None,
        responder: Responder | None = None,
    ) -> None:
        # One shared session across every composed service, so a dispatched
        # mutation and its Activity event commit atomically.
        self._session = session
        self._projects = ProjectsService(session)
        self._tasks = TasksService(session)
        self._activity = ActivityService(session)
        # Resolver is injectable so tests can pin behavior; defaults to the
        # deterministic v1 brain.
        self._resolver: Resolver = resolver or HardcodedResolver()
        # Responder renders outcomes into words; injectable so a later
        # LlmResponder can swap in behind the Protocol without touching
        # dispatch. Defaults to the deterministic grounded template.
        self._responder: Responder = responder or TemplateResponder()

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
        return WorldView(projects=project_refs, tasks=tuple(task_refs))

    async def handle(
        self,
        current_user: User,
        message: str,
        timezone_name: str | None = None,
    ) -> ConversationResponse:
        world = await self._build_world(current_user)

        # Resolver proposes; it executes nothing.
        try:
            action = self._resolver.resolve(message, world)
        except NoMatchError:
            return self._respond(
                Outcome(kind="no_match", executed=False)
            )
        except CompletionTargetNotFoundError as exc:
            return self._respond(
                Outcome(
                    kind="target_not_found",
                    executed=False,
                    target=exc.target,
                )
            )
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
        return self._respond(outcome)

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

        if action.action == registry.TASK_LIST:
            assert action.project_id is not None
            tasks = await self._tasks.list_tasks(
                current_user, action.project_id
            )
            active = [t for t in tasks if t.status == "active"]
            return Outcome(
                kind="task_list",
                executed=True,
                action=action.action,
                task_total=len(tasks),
                task_active=len(active),
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

        # Registry membership was checked upstream; reaching here is a bug.
        raise UnknownActionError(action.action)

    def _window_recent(
        self, activities: list[Activity]
    ) -> list[Activity]:
        """Most-recent-first, within the recall window, capped. Coerces naive
        timestamps to UTC so the SQLite harness (which may return naive
        datetimes) compares cleanly against an aware cutoff."""
        cutoff = datetime.now(timezone.utc) - self._RECALL_WINDOW

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

        now_local = datetime.now(timezone.utc).astimezone(user_tz)
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

    def _entity_label(
        self, activity: Activity, world: WorldView
    ) -> str | None:
        """A human label for the entity an event concerned. Prefers a title
        carried in the payload, then resolves against the WorldView. If the
        entity is no longer resolvable, returns None so the responder can use
        a neutral phrase without leaking storage identifiers."""
        payload = activity.payload or {}
        for key in ("title", "task_title", "name"):
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
        return None
