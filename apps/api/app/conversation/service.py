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
from app.conversation.context import (
    ConversationContextStore,
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


class ConversationService:
    """Orchestrates one conversational turn over the peer capabilities."""

    def __init__(
        self,
        session: AsyncSession,
        resolver: Resolver | None = None,
        responder: Responder | None = None,
        understanding_provider: UnderstandingProvider | None = None,
        context_store: ConversationContextStore | None = None,
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
        return WorldView(projects=project_refs, tasks=tuple(task_refs))

    async def handle(
        self,
        current_user: User,
        message: str,
        timezone_name: str | None = None,
    ) -> ConversationResponse:
        world = await self._build_world(current_user)

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
            result = await self._understanding_provider.understand(
                message=message,
                world=world,
                context=self._context_payload(current_user),
            )
        except UnderstandingProviderError as exc:
            logger.warning(
                "Conversation understanding provider failed",
                extra={"provider_error_code": exc.code},
            )
            return fallback

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

        # Registry membership was checked upstream; reaching here is a bug.
        raise UnknownActionError(action.action)

    def _resolved_action_from_proposal(
        self, proposal: ActionProposal, world: WorldView
    ) -> ResolvedAction:
        if proposal.action == registry.TASK_UPDATE:
            status = (proposal.arguments or {}).get("status")
            if status != "complete":
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
            return ResolvedAction(
                action=registry.ACTIVITY_RECALL,
                recall_window=proposal.recall_window,
            )

        if proposal.action == registry.PROJECT_LIST:
            return ResolvedAction(action=registry.PROJECT_LIST)

        if proposal.action == registry.TASK_LIST:
            project = self._resolve_project_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=registry.TASK_LIST,
                project_id=project.project_id,
            )

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

    def _context_payload(self, current_user: User) -> dict | None:
        if self._context_store is None:
            return None
        ref = self._context_store.get_last_task(current_user.id)
        if ref is None:
            return None
        return {
            "last_grounded_entity": {
                "kind": "task",
                "title": ref.title,
                "project": ref.project_name,
            }
        }

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
