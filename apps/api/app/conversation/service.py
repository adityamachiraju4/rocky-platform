"""ConversationService: the orchestrator and the trust boundary.

Flow for one turn:

    route without personal data
        -> deterministic Rocky action fast path
        -> deterministic live-information fast path
        -> model-backed route/general-answer decision
        -> build an owned WorldView only for a selected personal/action route
        -> validate any action against the closed registry
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
import re
import time
import uuid
from dataclasses import replace
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
from app.memories.service import MemoriesService
from app.memories.schemas import MemoryCreate, MemoryUpdate
from app.memories.exceptions import MemoryNotFoundError, InvalidMemoryTransitionError
from app.conversation.memory_intent import explicit_memory_operation
from app.conversation.plans.memory_confirmation import MemoryForgetPlan
from app.conversation.plans.base import ProposedPlanStep
from app.conversation.resolver import MemoryRef
from app.lists.schemas import ListCreate, ListItemCreate, ListItemUpdate, ListUpdate
from app.lists.service import ListsService
from app.live import registry as live_registry
from app.live.responder import render_live_result
from app.live.service import LiveIntelligenceService
from app.live.intent import (
    LiveIntent,
    canonical_live_reference,
    resolve_live_follow_up,
)
from app.live.temporal import interpret_temporal

from app.conversation import registry
from app.conversation.actions.base import (
    ActionResult,
    ExecutableAction,
    ExecutorKey,
    GroundedResultReference,
    ReferencePolicy,
)
from app.conversation.actions.list import (
    ListAddItemArgs,
    ListCompleteItemArgs,
    ListCreateArgs,
)
from app.conversation.actions.note import (
    NoteCreateArgs,
    NoteListArgs,
    NoteUpdateArgs,
)
from app.conversation.actions.notification import NotificationListArgs
from app.conversation.actions.project import ProjectCreateArgs
from app.conversation.actions.reminder import ReminderCreateArgs
from app.conversation.actions.runtime import validate_resolved_action
from app.conversation.actions.task import TaskCreateArgs, TaskUpdateArgs
from app.conversation.context import (
    ConversationContextStore,
    DurableReference,
    RecentTurn,
)
from app.models.conversation import ConversationThread, PendingConversationPlan
from app.conversation.exceptions import (
    AmbiguousReferenceError,
    CompletionTargetNotFoundError,
    NoMatchError,
    UnknownActionError,
)
from app.conversation.language import (
    is_general_follow_up,
    live_information_category,
    live_information_reply,
    normalize_capability_message,
    response_language,
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
from app.conversation.schemas import ConversationResponse, LocationContext, ResolvedAction
from app.conversation.responder import (
    Outcome,
    RecallFact,
    Responder,
    TemplateResponder,
)
from app.conversation.personal_context import PersonalContextRetriever
from app.conversation.recovery import (
    MAX_UNDERSTANDING_PASSES, ContextSource, PassKind, UnderstandingPassState,
    recovery_context,
)
from app.conversation.understanding import (
    Clarification,
    ConversationTurn,
    PersonalContextRequest,
    UnderstandingProvider,
    UnderstandingProviderError,
    UnderstandingResult,
    Unsupported,
    ActionProposal,
    PlanProposal,
)
from app.conversation.plans.base import ExecutablePlan, ExecutablePlanStep, PlanResult, PlanStatus
from app.conversation.plans.compiler import PlanCompiler, PlanValidationError
from app.conversation.plans.executor import PlanExecutor
from app.conversation.plans.presentation import confirmation_reply, durable_result_payload, result_reply
from app.conversation.plans.presentation import pending_plan_summary
from app.conversation.plans.store import (
    PendingPlanConflictError,
    PendingPlanStore,
)
from app.intelligence.decision import (
    ConfirmationKind,
    DecisionPolicy,
    DecisionProvider,
    DecisionProviderError,
    RouteDecision,
    RouteKind,
)

logger = logging.getLogger(__name__)

_LIVE_INFORMATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "weather",
        re.compile(
            r"\b(?:what(?:'s| is) the weather|weather (?:today|now|tomorrow|"
            r"this week|in\b)|forecast (?:for|today|tomorrow))",
            re.IGNORECASE,
        ),
    ),
    (
        "news",
        re.compile(
            r"\b(?:latest|current|today(?:'s)?)\s+(?:\w+\s+){0,3}news\b|"
            r"\bnews today\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prices",
        re.compile(
            r"\b(?:current|latest|today(?:'s)?)\s+"
            r"(?:price|stock price|exchange rate)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sports scores",
        re.compile(
            r"\b(?:live|latest|current|today(?:'s)?)\s+"
            r"(?:score|scores|standings)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "traffic",
        re.compile(
            r"\b(?:traffic|travel time)\b.*\b(?:now|today|current)\b|"
            r"\b(?:current|live)\s+traffic\b",
            re.IGNORECASE,
        ),
    ),
)


def _with_location_context(
    intent: LiveIntent,
    location_context: LocationContext | None,
) -> LiveIntent:
    if location_context is None or intent.tool_name not in {
        live_registry.WEATHER_CURRENT,
        live_registry.WEATHER_FORECAST,
        live_registry.PLACES_SEARCH,
    }:
        return intent

    current_location = str(intent.arguments.get("location") or "").strip()
    if current_location:
        return intent

    return LiveIntent(
        intent.tool_name,
        {
            **intent.arguments,
            "location": "Current location",
            "latitude": location_context.latitude,
            "longitude": location_context.longitude,
        },
    )


def _target_type_for_action(
    action: str,
) -> str:
    definition = registry.definition(action)
    return definition.reference_kind or action.partition(".")[0]


ACTION_EXECUTOR_METHODS: dict[ExecutorKey, str] = {
    ExecutorKey.PROJECT_LIST: "_execute_project_list",
    ExecutorKey.PROJECT_CREATE: "_execute_project_create",
    ExecutorKey.TASK_LIST: "_execute_task_list",
    ExecutorKey.TASK_CREATE: "_execute_task_create",
    ExecutorKey.TASK_UPDATE: "_execute_task_update",
    ExecutorKey.ACTIVITY_RECALL: "_execute_activity_recall",
    ExecutorKey.REMINDER_CREATE: "_execute_reminder_create",
    ExecutorKey.REMINDER_LIST: "_execute_reminder_list",
    ExecutorKey.REMINDER_COMPLETE: "_execute_reminder_complete",
    ExecutorKey.REMINDER_CANCEL: "_execute_reminder_cancel",
    ExecutorKey.NOTIFICATION_LIST: "_execute_notification_list",
    ExecutorKey.NOTIFICATION_READ: "_execute_notification_read",
    ExecutorKey.NOTIFICATION_DISMISS: "_execute_notification_dismiss",
    ExecutorKey.MEMORY_REMEMBER: "_execute_memory_remember",
    ExecutorKey.MEMORY_LIST: "_execute_memory_list",
    ExecutorKey.MEMORY_UPDATE: "_execute_memory_update",
    ExecutorKey.MEMORY_FORGET: "_execute_memory_forget",
    ExecutorKey.NOTE_CREATE: "_execute_note_create",
    ExecutorKey.NOTE_LIST: "_execute_note_list",
    ExecutorKey.NOTE_UPDATE: "_execute_note_update",
    ExecutorKey.NOTE_ARCHIVE: "_execute_note_archive",
    ExecutorKey.LIST_CREATE: "_execute_list_create",
    ExecutorKey.LIST_LIST: "_execute_list_list",
    ExecutorKey.LIST_ADD_ITEM: "_execute_list_add_item",
    ExecutorKey.LIST_COMPLETE_ITEM: "_execute_list_complete_item",
    ExecutorKey.LIST_ARCHIVE: "_execute_list_archive",
}


class ConversationService:
    """Orchestrates one conversational turn over the peer capabilities."""

    _LIVE_REFERENCE_MAX_AGE = timedelta(hours=24)

    def __init__(
        self,
        session: AsyncSession,
        resolver: Resolver | None = None,
        responder: Responder | None = None,
        understanding_provider: UnderstandingProvider | None = None,
        decision_provider: DecisionProvider | None = None,
        decision_policy: DecisionPolicy | None = None,
        live_service: LiveIntelligenceService | None = None,
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
        self._memories = MemoriesService(session, clock=clock)
        self._lists = ListsService(session, clock=clock)
        self._personal_context = PersonalContextRetriever(
            projects=self._projects, tasks=self._tasks, reminders=self._reminders,
            notifications=self._notifications, notes=self._notes, lists=self._lists,
        )
        # Resolver is injectable so tests can pin behavior; defaults to the
        # deterministic v1 brain.
        self._resolver: Resolver = resolver or HardcodedResolver()
        # Responder renders outcomes into words; injectable so a later
        # LlmResponder can swap in behind the Protocol without touching
        # dispatch. Defaults to the deterministic grounded template.
        self._responder: Responder = responder or TemplateResponder()
        self._understanding_provider = understanding_provider
        self._decision_provider = decision_provider
        self._decision_policy = decision_policy or DecisionPolicy()
        self._live_service = live_service
        self._context_store = context_store or ConversationContextStore(session)
        self._plan_compiler = PlanCompiler()
        self._plan_executor = PlanExecutor()
        self._pending_plans = PendingPlanStore(session)

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

    async def create_thread(
        self, current_user: User, *, title: str | None = None
    ) -> ConversationThread:
        return await self._context_store.create_thread(
            current_user.id, title=title
        )

    async def handle(
        self,
        current_user: User,
        message: str,
        thread_id: uuid.UUID | None = None,
        timezone_name: str | None = None,
        language: str | None = None,
        location_context: LocationContext | None = None,
    ) -> ConversationResponse:
        owner_id = current_user.id
        thread = await self._context_store.resolve_thread(
            owner_id, thread_id
        )
        self._current_thread_id = thread.id
        self._current_references = await self._context_store.references(thread.id)
        self._current_recent_turns = await self._context_store.recent_turns(thread.id)
        turn_language = response_language(message, language)
        await self._context_store.add_turn(
            thread,
            role="user",
            content=message,
            language=turn_language,
        )
        pending = await self._pending_plans.current(
            current_user.id, thread.id, ensure_utc(self._clock.now())
        )
        if pending is not None:
            response = await self._handle_pending_plan(
                current_user, message, pending, timezone_name, turn_language
            )
        elif self._confirmation_phrase(message) in (
            self._CONFIRMATIONS | self._REJECTIONS
        ):
            response = ConversationResponse(
                executed=False, thread_id=thread.id, action="plan",
                reply="There isn't a pending plan to confirm or cancel.",
                language=turn_language,
            )
        else:
            response = await self._handle_turn(
                current_user,
                message,
                timezone_name=timezone_name,
                language=language,
                location_context=location_context,
            )
        if getattr(self, "_plan_lifecycle_rolled_back", False):
            thread = await self._context_store.resolve_thread(
                owner_id, self._current_thread_id
            )
        response = response.model_copy(update={"thread_id": thread.id})
        await self._context_store.add_turn(
            thread,
            role="assistant",
            content=response.reply,
            language=response.language,
            metadata={
                "executed": response.executed,
                "action": response.action,
            },
        )
        await self._context_store.set_reference(
            thread.id,
            kind="prior_result",
            entity_id=None,
            display_text=response.reply[:2000],
            metadata={"message": message[:2000], "language": response.language},
        )
        return response

    _CONFIRMATIONS = frozenset({"yes", "confirm", "go ahead", "proceed", "yes, proceed"})
    _REJECTIONS = frozenset({"no", "cancel", "don't do that", "do not do that", "reject"})

    @staticmethod
    def _confirmation_phrase(message: str) -> str:
        return re.sub(r"[.!?]+$", "", message.strip().lower()).strip()

    async def _handle_pending_plan(
        self,
        current_user: User,
        message: str,
        pending: PendingConversationPlan,
        timezone_name: str | None,
        turn_language: str,
    ) -> ConversationResponse:
        now = ensure_utc(self._clock.now())
        if pending.status == "expired":
            logger.info("Plan expired", extra={"plan_id": str(pending.id), "thread_id": str(self._current_thread_id)})
            return ConversationResponse(
                executed=False, thread_id=self._current_thread_id, action="plan",
                reply="That plan expired without being run. Please repeat the request if you still want me to do it.",
                language=turn_language,
            )

        phrase = self._confirmation_phrase(message)
        confirmation_kind: ConfirmationKind | None = None
        if phrase in self._CONFIRMATIONS:
            confirmation_kind = ConfirmationKind.CONFIRM
        elif phrase in self._REJECTIONS:
            confirmation_kind = ConfirmationKind.REJECT
        elif (
            self._decision_provider is not None
            and self._decision_policy.confirmation_enabled
        ):
            try:
                proposed = self._pending_plans.proposed(pending)
                decision = await self._decision_provider.classify_confirmation(
                    message, pending_plan_summary(proposed)
                )
            except DecisionProviderError as exc:
                logger.info(
                    "typesafe_decision_fallback",
                    extra={
                        "decision_type": "confirmation",
                        "provider_error": type(exc).__name__,
                        "fallback_used": True,
                    },
                )
                return self._ambiguous_confirmation_response(turn_language)
            if decision.confidence < self._decision_policy.confirmation_min_confidence:
                logger.info(
                    "typesafe_low_confidence",
                    extra={
                        "decision_type": "confirmation",
                        "confidence": decision.confidence,
                        "fallback_used": True,
                    },
                )
                return self._ambiguous_confirmation_response(turn_language)
            confirmation_kind = decision.kind

        if confirmation_kind is ConfirmationKind.REJECT:
            await self._pending_plans.reject(pending, now)
            logger.info("Plan rejected", extra={"plan_id": str(pending.id), "thread_id": str(self._current_thread_id)})
            return ConversationResponse(
                executed=False, thread_id=self._current_thread_id, action="plan",
                reply="Okay, I cancelled that plan and did not run any of it.",
                language=turn_language,
            )

        if confirmation_kind is ConfirmationKind.UNCERTAIN:
            return self._ambiguous_confirmation_response(turn_language)

        if confirmation_kind is ConfirmationKind.UNRELATED:
            return await self._handle_turn(
                current_user, message, timezone_name=timezone_name,
                language=turn_language,
            )

        if confirmation_kind is not ConfirmationKind.CONFIRM:
            # Conservative amendment behavior: the old plan can never be
            # confirmed after a new instruction. Route the full new turn.
            await self._pending_plans.reject(pending, now)
            logger.info("Pending plan invalidated by new instruction", extra={"plan_id": str(pending.id)})
            return await self._handle_turn(
                current_user, message, timezone_name=timezone_name,
                language=turn_language,
            )

        if not await self._pending_plans.claim(pending, now):
            return ConversationResponse(
                executed=False, thread_id=self._current_thread_id, action="plan",
                reply="That plan has already been handled or is no longer available.",
                language=turn_language,
            )

        logger.info("Plan confirmed", extra={"plan_id": str(pending.id), "thread_id": str(self._current_thread_id)})
        try:
            proposed = self._pending_plans.proposed(pending)
            plan = self._plan_compiler.compile(proposed, plan_id=pending.id)
            world = await self._world_for_plan(current_user, plan)
            await self._preflight_plan(current_user, plan, world)
            result = await self._execute_plan(current_user, plan, timezone_name)
        except Exception as exc:  # noqa: BLE001 - confirmed plans fail closed
            steps = pending.plan_payload.get("steps", [])
            memory_plan = isinstance(steps, list) and any(
                isinstance(step, dict) and str(step.get("action", "")).startswith("memory.")
                for step in steps
            )
            logger.warning("Confirmed plan revalidation failed",
                           extra={"plan_id": str(pending.id), "failure": type(exc).__name__},
                           exc_info=not memory_plan)
            terminal_persisted = await self._finish_pending_plan(
                pending,
                status="failed",
                result_payload={
                    "status": "invalid", "failure": type(exc).__name__
                },
            )
            return ConversationResponse(
                executed=False, thread_id=self._current_thread_id, action="plan",
                reply=(
                    "I couldn't safely revalidate that plan, so I did not run it."
                    + (
                        " I also couldn't save its final status; it remains "
                        "non-confirmable and will not be retried."
                        if not terminal_persisted else ""
                    )
                ),
                language=turn_language,
            )

        if any(
            step.failure == "result_persistence_failed"
            for step in result.steps
        ) and self._session.in_transaction():
            # The mutation committed inside its authoritative domain service.
            # Clear only the failed grounding transaction before recording the
            # honest terminal partial state; never retry the action.
            plan_id = pending.id
            await self._session.rollback()
            self._plan_lifecycle_rolled_back = True
            reloaded = await self._session.get(PendingConversationPlan, plan_id)
            if reloaded is not None:
                pending = reloaded

        terminal_status = "completed" if result.status is PlanStatus.SUCCESS else "failed"
        terminal_persisted = await self._finish_pending_plan(
            pending, status=terminal_status,
            result_payload=durable_result_payload(result),
        )
        reply = result_reply(result)
        if not terminal_persisted:
            reply += " I couldn't save the final plan status; it will not be retried."
        return ConversationResponse(
            executed=bool(result.completed_steps), thread_id=self._current_thread_id,
            action="plan", reply=reply, language=turn_language,
        )

    def _ambiguous_confirmation_response(
        self, turn_language: str
    ) -> ConversationResponse:
        return ConversationResponse(
            executed=False,
            thread_id=self._current_thread_id,
            action="plan",
            reply=(
                "I'm not confident whether you want me to run the pending plan. "
                "Please say 'confirm' to run all of it or 'cancel' to reject it."
            ),
            language=turn_language,
        )

    async def _finish_pending_plan(
        self,
        pending: PendingConversationPlan,
        *,
        status: str,
        result_payload: dict[str, object],
    ) -> bool:
        plan_id = str(pending.id)
        try:
            await self._pending_plans.finish(
                pending, status=status, result_payload=result_payload,
                now=ensure_utc(self._clock.now()),
            )
        except Exception:  # noqa: BLE001 - executing remains fail-closed
            if self._session.in_transaction():
                await self._session.rollback()
            self._plan_lifecycle_rolled_back = True
            logger.error(
                "Plan terminal lifecycle persistence failed",
                extra={"plan_id": plan_id, "terminal_status": status},
                exc_info=True,
            )
            return False
        return True

    async def _handle_turn(
        self,
        current_user: User,
        message: str,
        timezone_name: str | None = None,
        language: str | None = None,
        location_context: LocationContext | None = None,
    ) -> ConversationResponse:
        turn_language = response_language(message, language)
        resolver_message = normalize_capability_message(message)
        empty_world = WorldView(projects=(), tasks=())
        grounded_world: WorldView | None = None

        # The deterministic resolver intentionally recognizes one action. A
        # conservative multi-verb gate prevents it from greedily treating an
        # entire multi-action request as one title, while ordinary requests
        # retain the existing zero-provider fast path.
        if (
            self._understanding_provider is not None
            and self._looks_like_multi_action(message)
        ):
            route = await self._route_decision(message)
            routed = await self._apply_route_decision(
                current_user, route, turn_language
            )
            if isinstance(routed, Outcome):
                return self._respond(routed, turn_language)
            outcome = await self._try_provider(
                current_user, message,
                routed if isinstance(routed, WorldView) else None,
                timezone_name, turn_language,
                fallback=Outcome(kind="no_match", executed=False),
            )
            return self._respond(outcome, turn_language)

        # Resolver proposes; it executes nothing. Deterministic handling stays
        # first so obvious/offline cases never depend on a model provider. It
        # first receives an empty world: this recognizes command shape without
        # loading private data. Reference-dependent commands request a grounded
        # retry below.
        try:
            action = self._resolver.resolve(resolver_message, empty_world)
        except NoMatchError:
            if self._live_service is not None:
                temporal = interpret_temporal(
                    message,
                    clock=self._clock,
                    timezone_name=timezone_name or current_user.timezone,
                )
                live_request = self._live_service.resolve_request(
                    message,
                    temporal=temporal,
                )
                if live_request is not None and live_request.clarification:
                    logger.info(
                        "live_temporal_unsupported",
                        extra={
                            "live_category": live_request.category,
                            "temporal_kind": (
                                temporal.kind if temporal is not None else None
                            ),
                            "relative_label": (
                                temporal.relative_label
                                if temporal is not None
                                else None
                            ),
                            "timezone": (
                                temporal.timezone if temporal is not None else None
                            ),
                            "routing_source": "standalone",
                            "supported_by_capability": False,
                        },
                    )
                    return ConversationResponse(
                        executed=False,
                        thread_id=self._current_thread_id,
                        reply=live_request.clarification,
                        language=turn_language,
                    )
                live_intent = (
                    live_request.intent if live_request is not None else None
                )
                routing_source = "standalone"
                if live_intent is None:
                    live_reference = self._fresh_live_reference("live_subject")
                    place_reference = self._fresh_live_reference("place")
                    follow_up = resolve_live_follow_up(
                        message,
                        live_subject=(
                            live_reference.metadata if live_reference else None
                        ),
                        place=(place_reference.metadata if place_reference else None),
                        temporal=temporal,
                    )
                    if follow_up is not None and follow_up.clarification:
                        logger.info(
                            "live_followup_ambiguous",
                            extra={
                                "live_category": follow_up.category,
                                "routing_source": "durable_reference",
                            },
                        )
                        return ConversationResponse(
                            executed=False,
                            thread_id=self._current_thread_id,
                            reply=follow_up.clarification,
                            language=turn_language,
                        )
                    if follow_up is not None:
                        live_intent = follow_up.intent
                        routing_source = "durable_reference"
                        if live_intent is not None:
                            logger.info(
                                "live_followup_resolved",
                                extra={
                                    "live_tool": live_intent.tool_name,
                                    "live_category": follow_up.category,
                                    "routing_source": routing_source,
                                    "context_inherited": True,
                                    "explicit_override": follow_up.explicit_override,
                                },
                            )
                if live_intent is not None:
                    return await self._execute_live_intent(
                        live_intent,
                        location_context=location_context,
                        turn_language=turn_language,
                    )
            live_category = (
                live_information_category(message)
                or self._live_information_category(message)
            )
            if live_category is not None:
                return self._respond(
                    Outcome(
                        kind="conversation",
                        executed=False,
                        reply=live_information_reply(
                            live_category, turn_language
                        ),
                    ),
                    turn_language,
                )
            routed_world: WorldView | None = None
            if self._route_decision_may_help(message):
                route = await self._route_decision(message)
                routed = await self._apply_route_decision(
                    current_user, route, turn_language
                )
                if isinstance(routed, Outcome):
                    return self._respond(routed, turn_language)
                if isinstance(routed, WorldView):
                    routed_world = routed
            outcome = await self._try_provider(
                current_user,
                message,
                routed_world,
                timezone_name,
                turn_language,
                fallback=Outcome(kind="no_match", executed=False),
            )
            return self._respond(outcome, turn_language)
        except CompletionTargetNotFoundError:
            # Empty-world resolution proved that this looks like a Rocky
            # command whose reference must be grounded. Load personal data
            # only now, then retry the deterministic resolver.
            grounded_world = await self._load_world(current_user)
            try:
                action = self._resolver.resolve(
                    resolver_message, grounded_world
                )
            except NoMatchError:
                outcome = await self._try_provider(
                    current_user,
                    message,
                    grounded_world,
                    timezone_name,
                    turn_language,
                    fallback=Outcome(kind="no_match", executed=False),
                )
                return self._respond(outcome, turn_language)
            except CompletionTargetNotFoundError as grounded_exc:
                outcome = await self._try_provider(
                    current_user,
                    message,
                    grounded_world,
                    timezone_name,
                    turn_language,
                    fallback=self._target_not_found_outcome(
                        message, grounded_exc.target
                    ),
                )
                return self._respond(outcome, turn_language)
            except AmbiguousReferenceError as grounded_exc:
                return self._respond(
                    Outcome(
                        kind="ambiguous",
                        executed=False,
                        candidates=tuple(grounded_exc.candidates),
                    ),
                    turn_language,
                )
        except AmbiguousReferenceError as exc:
            return self._respond(
                Outcome(
                    kind="ambiguous",
                    executed=False,
                    candidates=tuple(exc.candidates),
                ),
                turn_language,
            )

        # Trust boundary: the proposed action must be in the closed registry.
        if not registry.is_allowed(action.action):
            raise UnknownActionError(action.action)

        memory_denial = self._memory_intent_denial((action.action,), message)
        if memory_denial is not None:
            return self._respond(memory_denial, turn_language)
        if grounded_world is None and self._action_requires_world(action.action):
            grounded_world = await self._load_world(current_user)
        world = grounded_world or empty_world
        outcome = await self._dispatch(
            current_user, action, world, timezone_name, original_request=message
        )
        await self._remember_outcome(outcome)
        return self._respond(outcome, turn_language)

    async def _execute_live_intent(
        self,
        intent: LiveIntent,
        *,
        location_context: LocationContext | None,
        turn_language: str,
    ) -> ConversationResponse:
        assert self._live_service is not None
        resolved_intent = _with_location_context(intent, location_context)
        result = await self._live_service.execute(resolved_intent)
        if result.succeeded:
            await self._remember_live_intent(resolved_intent)
        return ConversationResponse(
            executed=False,
            thread_id=self._current_thread_id,
            action=result.tool_name,
            reply=render_live_result(result),
            language=turn_language,
        )

    async def _remember_live_intent(self, intent: LiveIntent) -> None:
        display_text, metadata = canonical_live_reference(intent)
        await self._context_store.set_reference(
            self._current_thread_id,
            kind="live_subject",
            entity_id=None,
            display_text=display_text,
            metadata=metadata,
        )
        if intent.tool_name == live_registry.PLACES_SEARCH:
            await self._context_store.set_reference(
                self._current_thread_id,
                kind="place",
                entity_id=None,
                display_text=display_text,
                metadata=metadata,
            )
        self._current_references = await self._context_store.references(
            self._current_thread_id
        )

    async def _try_provider(
        self,
        current_user: User,
        message: str,
        world: WorldView | None,
        timezone_name: str | None,
        response_language: str,
        fallback: Outcome,
    ) -> Outcome:
        if self._understanding_provider is None:
            return fallback

        state = UnderstandingPassState(
            ContextSource.TRUSTED if world is not None else ContextSource.NONE
        )
        context = self._context_payload(
            current_user,
            include_personal_context=world is not None,
            include_general_context=world is None and is_general_follow_up(message),
        )
        for _ in range(MAX_UNDERSTANDING_PASSES):
            provider_started = time.perf_counter()
            try:
                result = await self._understanding_provider.understand(
                    message=message,
                    world=world,
                    context=context,
                    include_personal_context=world is not None,
                    response_language=response_language,
                )
                if isinstance(result, Clarification):
                    result.validated()
                if isinstance(result, PersonalContextRequest) and state.can_retrieve:
                    world = await self._personal_context.retrieve(current_user, result)
                    state = state.after_retrieval()
                    context = self._context_payload(current_user, include_personal_context=True)
                    continue
            except UnderstandingProviderError as exc:
                logger.warning(
                    "Conversation understanding provider failed: elapsed_ms=%.1f",
                    (time.perf_counter() - provider_started) * 1000,
                    extra={"provider_error_code": exc.code},
                )
                if state.kind is PassKind.RECOVERY:
                    self._log_recovery(state, "provider_error", provider_started, fallback_used=True)
                if fallback.kind == "no_match":
                    return Outcome(
                        kind="conversation", executed=False,
                        reply="I couldn't answer that right now. Please try again.",
                    )
                return fallback
            logger.info(
                "Conversation understanding provider complete: elapsed_ms=%.1f",
                (time.perf_counter() - provider_started) * 1000,
            )
            if state.kind is PassKind.RECOVERY:
                self._log_recovery(
                    state, result.kind, provider_started,
                    candidate_count=len(result.candidates) if isinstance(result, Clarification) else 0,
                )
            if isinstance(result, PersonalContextRequest):
                # Neither a grounded nor recovery pass may retrieve again.
                return Outcome(
                    kind="unsupported", executed=False,
                    reply="I couldn't ground that request in your Rocky data.",
                )
            if isinstance(result, Clarification) and state.kind is not PassKind.RECOVERY:
                additional_context = None
                if state.can_recover:
                    additional_context = recovery_context(
                        context,
                        self._context_payload(
                            current_user, include_personal_context=False,
                            include_general_context=True,
                        ),
                        result,
                    )
                if additional_context is not None:
                    state = state.after_recovery()
                    context = additional_context
                    continue
                self._log_recovery(
                    state, "clarification", provider_started,
                    candidate_count=len(result.candidates), attempted=False,
                )
            break
        else:  # State transitions above allow at most three finite passes.
            raise RuntimeError("Understanding pass budget exhausted")

        actions = (
            (result.action,) if isinstance(result, ActionProposal)
            else tuple(step.action for step in result.plan.steps) if isinstance(result, PlanProposal)
            else ()
        )
        memory_denial = self._memory_intent_denial(actions, message)
        if memory_denial is not None:
            return memory_denial
        if state.context_source is not ContextSource.TRUSTED and (
            (isinstance(result, ActionProposal) and not (
                result.action in {registry.MEMORY_REMEMBER, registry.MEMORY_LIST}
            ))
            or (
                isinstance(result, PlanProposal)
                and self._proposed_plan_requires_world(result)
            )
            or (isinstance(result, ConversationTurn) and result.reference)
        ):
            world = await self._load_world(current_user)

        if isinstance(result, ActionProposal) and result.action in {
            registry.MEMORY_UPDATE, registry.MEMORY_FORGET,
        }:
            world = await self._load_memory_world(current_user, world)
        if isinstance(result, ActionProposal) and world is None:
            world = WorldView(projects=(), tasks=())
        outcome = await self._outcome_from_understanding(
            current_user, result, world, timezone_name, message
        )
        await self._remember_outcome(outcome)
        return outcome

    @staticmethod
    def _log_recovery(
        state: UnderstandingPassState,
        result_kind: str,
        started: float,
        *,
        candidate_count: int = 0,
        attempted: bool = True,
        fallback_used: bool = False,
    ) -> None:
        logger.info("understanding_recovery", extra={
            "recovery_attempted": attempted,
            "recovery_source": state.context_source.value,
            "recovery_result_kind": result_kind,
            "retrieved_context_present": state.context_source is ContextSource.RETRIEVED,
            "candidate_count": candidate_count,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "fallback_used": fallback_used,
        })

    async def _outcome_from_understanding(
        self,
        current_user: User,
        result: UnderstandingResult,
        world: WorldView | None,
        timezone_name: str | None,
        original_request: str,
    ) -> Outcome:
        if isinstance(result, ConversationTurn):
            if result.reference:
                assert world is not None
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
                    reference_kind="task",
                    reference_id=task.task_id,
                    reference_label=task.title,
                    reference_metadata={
                        "project_id": str(task.project_id),
                        "project": task.project_name,
                    },
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
                reply=result.prompt or (None if result.candidates else "Could you clarify what you want me to do?"),
            )

        if isinstance(result, Unsupported):
            return Outcome(
                kind="unsupported",
                executed=False,
                reply=result.reason or "I can't do that yet.",
            )

        if isinstance(result, PlanProposal):
            return await self._outcome_from_plan(
                current_user, result, world, timezone_name, original_request
            )

        if not isinstance(result, ActionProposal):
            return Outcome(kind="no_match", executed=False)

        proposal = result
        assert world is not None
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
            current_user, action, world, timezone_name, original_request=original_request
        )

    @staticmethod
    def _proposed_plan_requires_world(proposal: PlanProposal) -> bool:
        return any(
            registry.definition(step.action).requires_world
            and step.result_of is None
            for step in proposal.plan.steps
            if registry.is_allowed(step.action)
        )

    async def _outcome_from_plan(
        self,
        current_user: User,
        proposal: PlanProposal,
        world: WorldView | None,
        timezone_name: str | None,
        original_request: str,
    ) -> Outcome:
        try:
            plan = self._plan_compiler.compile(proposal.plan)
            plan_world = world or await self._world_for_plan(current_user, plan)
            if world is not None and self._plan_needs_memories(plan):
                plan_world = await self._load_memory_world(current_user, plan_world)
            await self._preflight_plan(current_user, plan, plan_world)
        except (PlanValidationError, CompletionTargetNotFoundError, AmbiguousReferenceError) as exc:
            logger.info("Plan validation failed", extra={"failure": type(exc).__name__})
            return Outcome(
                kind="unsupported", executed=False,
                reply="I couldn't build a safe plan from that request. Please restate the steps and references.",
            )

        verification_block = await self._verify_plan(original_request, plan)
        if verification_block is not None:
            return verification_block

        logger.info(
            "Plan proposed",
            extra={"plan_id": str(plan.id), "step_count": len(plan.steps),
                   "actions": [step.definition.name for step in plan.steps]},
        )
        if plan.requires_confirmation:
            try:
                await self._pending_plans.create(
                    current_user.id, self._current_thread_id, plan,
                    ensure_utc(self._clock.now()),
                )
            except PendingPlanConflictError:
                logger.info(
                    "Plan creation lost a concurrent proposal",
                    extra={"plan_id": str(plan.id)},
                )
                return Outcome(
                    kind="conversation", executed=False, action="plan",
                    reply=(
                        "Another request updated the pending plan for this "
                        "conversation. Please review the latest plan before confirming."
                    ),
                )
            logger.info("Plan awaiting confirmation", extra={"plan_id": str(plan.id)})
            return Outcome(
                kind="conversation", executed=False, action="plan",
                reply=confirmation_reply(plan),
            )

        result = await self._execute_plan(current_user, plan, timezone_name)
        return Outcome(
            kind="conversation", executed=bool(result.completed_steps),
            action="plan", reply=result_reply(result),
        )

    async def _verify_plan(
        self, original_request: str, plan: ExecutablePlan
    ) -> Outcome | None:
        policy = self._decision_policy
        if not policy.plan_verification_enabled:
            return None
        if self._decision_provider is None:
            if policy.plan_verification_required:
                return self._plan_verification_blocked(
                    "I couldn't verify that plan with the required independent checker, so I did not run it."
                )
            return None
        try:
            decision = await self._decision_provider.verify_plan(
                original_request, plan
            )
        except DecisionProviderError as exc:
            logger.info(
                "typesafe_decision_fallback",
                extra={
                    "decision_type": "plan_verification",
                    "provider_error": type(exc).__name__,
                    "fallback_used": not policy.plan_verification_required,
                },
            )
            if policy.plan_verification_required:
                return self._plan_verification_blocked(
                    "I couldn't verify that plan with the required independent checker, so I did not run it."
                )
            return None

        concern_thresholds = {
            "unrequested_action": (
                policy.verification_unrequested_action_probability
            ),
            "omitted_action": policy.verification_omitted_action_probability,
            "excessive_mutation": (
                policy.verification_excessive_mutation_probability
            ),
        }
        blocking_concerns = {
            name: probability
            for name, probability in decision.concern_probabilities.items()
            if probability >= concern_thresholds[name]
        }
        if blocking_concerns:
            logger.info(
                "typesafe_plan_verification_concern",
                extra={
                    "decision_type": "plan_verification",
                    "concerns": blocking_concerns,
                    "fallback_used": False,
                },
            )
            return self._plan_verification_blocked(
                "The independent plan check found a possible mismatch with your request, so I did not run it. Please restate the exact steps you want."
            )

        clearly_faithful = (
            decision.faithful_probability
            >= policy.verification_faithful_probability
            and all(
                probability
                <= policy.verification_clear_concern_max_probability
                for probability in decision.concern_probabilities.values()
            )
        )
        if clearly_faithful:
            return None

        logger.info(
            "typesafe_ambiguous_decision",
            extra={
                "decision_type": "plan_verification",
                "faithful_probability": decision.faithful_probability,
                "concern_probabilities": decision.concern_probabilities,
                "fallback_used": not policy.plan_verification_required,
            },
        )
        if policy.plan_verification_required:
            return self._plan_verification_blocked(
                "I couldn't verify that plan confidently, so I did not run it."
            )
        return None

    @staticmethod
    def _plan_verification_blocked(reply: str) -> Outcome:
        return Outcome(
            kind="unsupported", executed=False, action="plan", reply=reply
        )

    @staticmethod
    def _plan_needs_memories(plan: ExecutablePlan) -> bool:
        return any(step.definition.reference_kind == "memory" and step.result_of is None
                   for step in plan.steps)

    async def _world_for_plan(
        self, current_user: User, plan: ExecutablePlan
    ) -> WorldView:
        world = (
            await self._load_world(current_user)
            if any(step.definition.requires_world and step.result_of is None
                   and step.definition.reference_kind != "memory" for step in plan.steps)
            else WorldView(projects=(), tasks=())
        )
        if self._plan_needs_memories(plan):
            world = await self._load_memory_world(current_user, world)
        return world

    async def _preflight_plan(
        self, current_user: User, plan: ExecutablePlan, world: WorldView
    ) -> None:
        """Ground every reference that can exist before execution."""

        for step in plan.steps:
            if step.result_of is None:
                self._resolved_action_from_plan_step(step, world, {})

    async def _execute_plan(
        self,
        current_user: User,
        plan: ExecutablePlan,
        timezone_name: str | None,
    ) -> PlanResult:
        async def prepare(step: ExecutablePlanStep, references: dict):
            world = (
                await self._load_world(current_user)
                if self._plan_step_needs_world(step)
                else WorldView(projects=(), tasks=())
            )
            if step.definition.reference_kind == "memory" and step.result_of is None:
                world = await self._load_memory_world(current_user, world)
            return self._resolved_action_from_plan_step(step, world, references), world

        async def execute(action: ResolvedAction, world: WorldView) -> ActionResult:
            return await self._execute_action(
                current_user, action, world, timezone_name
            )

        async def remember(result: ActionResult) -> None:
            if isinstance(result.outcome, Outcome):
                await self._remember_outcome(result.outcome)

        return await self._plan_executor.execute(
            plan, prepare_step=prepare, execute_step=execute,
            remember_result=remember,
        )

    @staticmethod
    def _plan_step_needs_world(step: ExecutablePlanStep) -> bool:
        if step.result_of is None:
            return step.definition.requires_world
        # A list-item completion must resolve the human item text inside the
        # newly created/grounded list. Other result references already carry
        # the Rocky-produced ID required by the authoritative domain service.
        return step.definition.name == registry.LIST_COMPLETE_ITEM

    def _resolved_action_from_plan_step(
        self,
        step: ExecutablePlanStep,
        world: WorldView,
        references: dict[str, tuple[GroundedResultReference, ...]],
    ) -> ResolvedAction:
        arguments = step.arguments.model_dump(exclude_none=True)
        if step.result_of is None:
            resolved = self._resolved_action_from_proposal(
                ActionProposal(
                    kind="action", action=step.definition.name,
                    reference=step.reference, arguments=arguments,
                ),
                world,
            )
            if step.definition.name == registry.TASK_CREATE and resolved.project_id is None:
                project = self._resolve_context_project(world)
                resolved = resolved.model_copy(
                    update={"project_id": project.project_id, "project_name": project.name}
                )
            return resolved

        candidates = references.get(step.result_of, ())
        matches = [
            reference for reference in candidates
            if reference.kind == step.definition.reference_kind
        ]
        if len(matches) != 1 or matches[0].entity_id is None:
            raise PlanValidationError("step result is unavailable or incompatible")
        reference = matches[0]
        action = step.definition.name

        if action in {registry.TASK_CREATE, registry.TASK_LIST}:
            return ResolvedAction(
                action=action, project_id=reference.entity_id,
                project_name=reference.display_text,
                task_title=arguments.get("title"),
            )
        if action == registry.TASK_UPDATE:
            project_id = reference.metadata.get("project_id")
            if not project_id:
                raise PlanValidationError("task result is missing its project grounding")
            return ResolvedAction(
                action=action, task_id=reference.entity_id,
                task_title=reference.display_text, project_id=uuid.UUID(project_id),
                project_name=reference.metadata.get("project"),
                status=arguments["status"],
            )
        if action in {registry.REMINDER_COMPLETE, registry.REMINDER_CANCEL}:
            return ResolvedAction(
                action=action, reminder_id=reference.entity_id,
                reminder_title=reference.display_text,
            )
        if action in {registry.NOTIFICATION_READ, registry.NOTIFICATION_DISMISS}:
            return ResolvedAction(action=action, notification_id=reference.entity_id)
        if action in {registry.MEMORY_UPDATE, registry.MEMORY_FORGET}:
            return ResolvedAction(
                action=action, memory_id=reference.entity_id,
                memory_reference_subject=reference.display_text, memory_subject=arguments.get("subject"),
                memory_kind=arguments.get("kind"), memory_content=arguments.get("content"),
            )
        if action in {registry.NOTE_UPDATE, registry.NOTE_ARCHIVE}:
            return ResolvedAction(
                action=action, note_id=reference.entity_id,
                note_title=arguments.get("title"), note_content=arguments.get("content"),
            )
        if action == registry.LIST_ADD_ITEM:
            return ResolvedAction(
                action=action, list_id=reference.entity_id,
                list_title=reference.display_text,
                list_item_content=arguments["content"],
            )
        if action == registry.LIST_COMPLETE_ITEM:
            values = [value for value in world.lists if value.list_id == reference.entity_id]
            if len(values) != 1 or values[0].status != "active":
                raise CompletionTargetNotFoundError(reference.display_text)
            item = self._resolve_list_item_reference(arguments["item"], values[0])
            return ResolvedAction(
                action=action, list_id=reference.entity_id,
                list_title=reference.display_text, list_item_id=item.item_id,
                list_item_content=item.content,
            )
        if action == registry.LIST_ARCHIVE:
            return ResolvedAction(
                action=action, list_id=reference.entity_id,
                list_title=reference.display_text,
            )
        raise PlanValidationError("action cannot consume a step result")

    async def _load_world(self, current_user: User) -> WorldView:
        """Load personal Rocky state only after routing selects that lane."""

        world_started = time.perf_counter()
        world = await self._build_world(current_user)
        logger.info(
            "Conversation world build complete: elapsed_ms=%.1f",
            (time.perf_counter() - world_started) * 1000,
        )
        return world

    @staticmethod
    def _target_not_found_outcome(message: str, target: str) -> Outcome:
        lowered = message.lower()
        target_type = (
            "memory"
            if "memory" in lowered or "remember" in lowered or "forget" in lowered
            else "reminder"
            if "reminder" in lowered
            else "notification"
            if "notification" in lowered
            else "note"
            if "note" in lowered
            else "list"
            if "list" in lowered
            else "task"
        )
        return Outcome(
            kind="target_not_found",
            executed=False,
            target=target,
            target_type=target_type,
        )

    def _respond(
        self, outcome: Outcome, language: str = "en"
    ) -> ConversationResponse:
        """Render an Outcome into the wire response. The reply string is
        the Responder's job; the service only carries executed/action."""
        return ConversationResponse(
            executed=outcome.executed,
            thread_id=self._current_thread_id,
            action=outcome.action,
            reply=self._responder.render(outcome, language=language),
            language=language,
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
        *,
        original_request: str | None = None,
    ) -> Outcome:
        if action.action == registry.MEMORY_FORGET:
            denial = self._memory_intent_denial((action.action,), original_request or "")
            if denial is not None:
                return denial
            proposed = MemoryForgetPlan(steps=[ProposedPlanStep(
                id="step_1", action=registry.MEMORY_FORGET, reference=action.memory_reference_subject,
                purpose=f"Forget memory: {action.memory_reference_subject}",
            )])
            return await self._outcome_from_plan(
                current_user, PlanProposal(kind="plan", plan=proposed), world,
                timezone_name, original_request or "",
            )
        result = await self._execute_action(
            current_user, action, world, timezone_name
        )
        assert isinstance(result.outcome, Outcome)
        return result.outcome

    async def _execute_action(
        self,
        current_user: User,
        action: ResolvedAction,
        world: WorldView,
        timezone_name: str | None = None,
    ) -> ActionResult:
        """Execute only through a registered definition and normalize facts."""

        executable = validate_resolved_action(action)
        try:
            executor_name = ACTION_EXECUTOR_METHODS[executable.definition.executor]
        except KeyError as exc:  # pragma: no cover - closed-registry guard
            raise UnknownActionError(action.action) from exc
        executor = getattr(self, executor_name)
        outcome = await executor(
            current_user, executable, world, timezone_name
        )
        references: tuple[GroundedResultReference, ...] = ()
        entity_ids: tuple[uuid.UUID, ...] = ()
        if outcome.reference_kind and outcome.reference_label:
            references = (
                GroundedResultReference(
                    kind=outcome.reference_kind,
                    entity_id=outcome.reference_id,
                    display_text=outcome.reference_label,
                    metadata=outcome.reference_metadata or {},
                ),
            )
            if outcome.reference_id is not None:
                entity_ids = (outcome.reference_id,)
        return ActionResult(
            success=outcome.executed,
            action=executable.definition.name,
            display_summary=(
                outcome.reply
                or ": ".join(
                    value for value in (
                        outcome.kind.replace("_", " "),
                        outcome.reference_label,
                    ) if value
                )
            ),
            payload={"kind": outcome.kind, "executed": outcome.executed},
            entity_ids=entity_ids,
            references=references,
            failure_type=None if outcome.executed else outcome.kind,
            outcome=outcome,
        )

    async def _execute_project_list(self, user: User, action: ExecutableAction,
                                    world: WorldView, timezone_name: str | None) -> Outcome:
        projects = await self._projects.list_projects(user)
        return Outcome(kind="project_list", executed=True, action=action.definition.name,
                       project_names=tuple(project.name for project in projects))

    async def _execute_project_create(self, user: User, action: ExecutableAction,
                                      world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, ProjectCreateArgs)
        project = await self._projects.create_project(
            user, ProjectCreate(name=action.arguments.name)
        )
        return Outcome(kind="project_created", executed=True,
                       action=action.definition.name, project_name=project.name,
                       reference_kind="project", reference_id=project.id,
                       reference_label=project.name)

    async def _execute_task_create(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, TaskCreateArgs)
        project_id = action.grounding.project_id
        project_name = action.grounding.project_name
        if project_id is None:
            try:
                project = self._resolve_context_project(world)
            except CompletionTargetNotFoundError as exc:
                return Outcome(kind="target_not_found", executed=False,
                               target=exc.target, target_type="project")
            except AmbiguousReferenceError as exc:
                return Outcome(kind="ambiguous", executed=False,
                               candidates=tuple(exc.candidates))
            project_id, project_name = project.project_id, project.name
        task = await self._tasks.create_task(
            user, project_id, TaskCreate(title=action.arguments.title)
        )
        return Outcome(
            kind="task_created", executed=True, action=action.definition.name,
            task_title=task.title, project_name=project_name,
            reference_kind="task", reference_id=task.id,
            reference_label=task.title,
            reference_metadata={"project_id": str(project_id),
                                "project": project_name or ""},
        )

    async def _execute_task_list(self, user: User, action: ExecutableAction,
                                 world: WorldView, timezone_name: str | None) -> Outcome:
        project_id = action.grounding.project_id
        if project_id is None:
            active = [task for task in world.tasks if task.status == "active"]
            return Outcome(
                kind="task_list", executed=True, action=action.definition.name,
                task_scope="all", task_total=len(world.tasks),
                task_active=len(active),
                task_titles=tuple(task.title for task in active[:5]),
                latest_completed_task_title=(
                    await self._latest_completed_task_title(user, world)
                ),
            )
        tasks = await self._tasks.list_tasks(user, project_id)
        active = [task for task in tasks if task.status == "active"]
        return Outcome(kind="task_list", executed=True,
                       action=action.definition.name, task_scope="project",
                       task_total=len(tasks), task_active=len(active),
                       task_titles=tuple(task.title for task in active[:5]))

    async def _execute_task_update(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, TaskUpdateArgs)
        assert action.grounding.project_id is not None
        assert action.grounding.task_id is not None
        task = await self._tasks.update_task(
            user, action.grounding.project_id, action.grounding.task_id,
            TaskUpdate(status=action.arguments.status),
        )
        return Outcome(
            kind="task_updated", executed=True, action=action.definition.name,
            task_title=task.title, task_status=task.status,
            project_name=next((item.project_name for item in world.tasks
                               if item.task_id == task.id), None),
            reference_kind="task", reference_id=task.id,
            reference_label=task.title,
            reference_metadata={"project_id": str(action.grounding.project_id)},
        )

    async def _execute_activity_recall(self, user: User, action: ExecutableAction,
                                       world: WorldView, timezone_name: str | None) -> Outcome:
        activities = await self._activity.list_activities(user)
        if action.grounding.recall_window == "yesterday":
            selected = self._window_yesterday(activities, timezone_name)
            window = "yesterday"
        else:
            selected = self._window_recent(activities)
            window = "recent"
        return Outcome(kind="activity_recall", executed=True,
                       action=action.definition.name,
                       recall_facts=self._recall_facts(selected, world),
                       recall_window=window)

    async def _execute_reminder_create(self, user: User, action: ExecutableAction,
                                       world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, ReminderCreateArgs)
        try:
            timezone_key = resolve_timezone(timezone_name or user.timezone).key
            due_at = interpret_reminder_time(
                action.arguments.when, timezone_key, self._clock
            )
        except (TimeError, ValueError) as exc:
            return Outcome(kind="unsupported", executed=False, reply=str(exc))
        reminder = await self._reminders.create_reminder(
            user, ReminderCreate(title=action.arguments.title, due_at=due_at,
                                 timezone=timezone_key)
        )
        return Outcome(kind="reminder_created", executed=True,
                       action=action.definition.name,
                       reminder_title=reminder.title,
                       reminder_due_at=reminder.due_at,
                       reminder_timezone=reminder.timezone,
                       reference_kind="reminder", reference_id=reminder.id,
                       reference_label=reminder.title)

    async def _execute_reminder_list(self, user: User, action: ExecutableAction,
                                     world: WorldView, timezone_name: str | None) -> Outcome:
        reminders = await self._reminders.list_reminders(user)
        open_reminders = [item for item in reminders
                          if item.status in {"scheduled", "due"}]
        single = open_reminders[0] if len(open_reminders) == 1 else None
        return Outcome(
            kind="reminder_list", executed=True, action=action.definition.name,
            reminders=tuple((item.title, item.due_at, item.timezone, item.status)
                            for item in open_reminders),
            reference_kind="reminder" if single else None,
            reference_id=single.id if single else None,
            reference_label=single.title if single else None,
        )

    async def _execute_reminder_complete(self, user: User, action: ExecutableAction,
                                         world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.reminder_id is not None
        reminder = await self._reminders.complete_reminder(
            user, action.grounding.reminder_id
        )
        return self._reminder_update_outcome(action, reminder)

    async def _execute_reminder_cancel(self, user: User, action: ExecutableAction,
                                       world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.reminder_id is not None
        reminder = await self._reminders.cancel_reminder(
            user, action.grounding.reminder_id
        )
        return self._reminder_update_outcome(action, reminder)

    @staticmethod
    def _reminder_update_outcome(action: ExecutableAction, reminder: object) -> Outcome:
        return Outcome(kind="reminder_updated", executed=True,
                       action=action.definition.name,
                       reminder_title=reminder.title,
                       reminder_status=reminder.status,
                       reference_kind="reminder", reference_id=reminder.id,
                       reference_label=reminder.title)

    async def _execute_notification_list(self, user: User, action: ExecutableAction,
                                         world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, NotificationListArgs)
        values = await self._notifications.list_notifications(
            user, status=action.arguments.status
        )
        visible = [item for item in values if item.status != "dismissed"]
        single = visible[0] if len(visible) == 1 else None
        return Outcome(
            kind="notification_list", executed=True, action=action.definition.name,
            notifications=tuple((item.title, item.body, item.status)
                                for item in visible),
            reference_kind="notification" if single else None,
            reference_id=single.id if single else None,
            reference_label=single.title if single else None,
        )

    async def _execute_notification_read(self, user: User, action: ExecutableAction,
                                         world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.notification_id is not None
        value = await self._notifications.mark_read(
            user, action.grounding.notification_id
        )
        return self._notification_update_outcome(action, value)

    async def _execute_notification_dismiss(self, user: User, action: ExecutableAction,
                                            world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.notification_id is not None
        value = await self._notifications.dismiss(
            user, action.grounding.notification_id
        )
        return self._notification_update_outcome(action, value)

    @staticmethod
    def _notification_update_outcome(action: ExecutableAction, value: object) -> Outcome:
        return Outcome(kind="notification_updated", executed=True,
                       action=action.definition.name,
                       notification_title=value.title,
                       notification_status=value.status,
                       reference_kind="notification", reference_id=value.id,
                       reference_label=value.title)

    @staticmethod
    def _memory_intent_denial(actions: tuple[str, ...], message: str) -> Outcome | None:
        for action in actions:
            if action in {registry.MEMORY_REMEMBER, registry.MEMORY_LIST, registry.MEMORY_UPDATE, registry.MEMORY_FORGET}:
                if not explicit_memory_operation(message, action):
                    return Outcome(kind="ambiguous", executed=False,
                                   reply="Please explicitly ask me to remember, list, update, or forget that personal memory.")
        return None

    async def _execute_memory_remember(self, user: User, action: ExecutableAction,
                                       world: WorldView, timezone_name: str | None) -> Outcome:
        memory = await self._memories.remember(
            user, MemoryCreate(**action.arguments.model_dump()),
            source_thread_id=getattr(self, "_current_thread_id", None),
        )
        return self._memory_outcome(action, memory)

    async def _execute_memory_list(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        values = await self._memories.list_memories(user, status="active")
        return Outcome(kind="memory_list", executed=True, action=action.definition.name,
                       memories=tuple((m.kind, m.subject, m.content) for m in values))

    async def _execute_memory_update(self, user: User, action: ExecutableAction,
                                     world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.memory_id is not None
        try:
            memory = await self._memories.update_memory(
                user, action.grounding.memory_id,
                MemoryUpdate(**action.arguments.model_dump(exclude_none=True)),
            )
        except (MemoryNotFoundError, InvalidMemoryTransitionError):
            return Outcome(kind="target_not_found", executed=False, target_type="memory",
                           target=action.grounding.memory_subject)
        return self._memory_outcome(action, memory)

    async def _execute_memory_forget(self, user: User, action: ExecutableAction,
                                     world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.memory_id is not None
        try:
            memory = await self._memories.forget(user, action.grounding.memory_id)
        except MemoryNotFoundError:
            return Outcome(kind="target_not_found", executed=False, target_type="memory",
                           target=action.grounding.memory_subject)
        return self._memory_outcome(action, memory)

    @staticmethod
    def _memory_outcome(action: ExecutableAction, memory: object) -> Outcome:
        return Outcome(kind="memory_changed", executed=True, action=action.definition.name,
                       memory_subject=memory.subject, memory_status=memory.status,
                       reference_kind="memory", reference_id=memory.id,
                       reference_label=memory.subject)

    async def _execute_note_create(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, NoteCreateArgs)
        note = await self._notes.create_note(
            user, NoteCreate(title=action.arguments.title,
                             content=action.arguments.content)
        )
        return Outcome(kind="note_created", executed=True,
                       action=action.definition.name, note_title=note.title,
                       reference_kind="note", reference_id=note.id,
                       reference_label=note.title)

    async def _execute_note_list(self, user: User, action: ExecutableAction,
                                 world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, NoteListArgs)
        status = action.arguments.status or "active"
        notes = await self._notes.list_notes(user, status=status)
        single = notes[0] if len(notes) == 1 else None
        return Outcome(kind="note_list", executed=True,
                       action=action.definition.name, note_status=status,
                       notes=tuple((note.title, note.content) for note in notes),
                       reference_kind="note" if single else None,
                       reference_id=single.id if single else None,
                       reference_label=single.title if single else None)

    async def _execute_note_update(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, NoteUpdateArgs)
        assert action.grounding.note_id is not None
        note = await self._notes.update_note(
            user, action.grounding.note_id,
            NoteUpdate.model_validate(action.arguments.model_dump(exclude_none=True)),
        )
        return self._note_update_outcome(action, note)

    async def _execute_note_archive(self, user: User, action: ExecutableAction,
                                    world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.note_id is not None
        note = await self._notes.update_note(
            user, action.grounding.note_id, NoteUpdate(status="archived")
        )
        return self._note_update_outcome(action, note)

    @staticmethod
    def _note_update_outcome(action: ExecutableAction, note: object) -> Outcome:
        return Outcome(kind="note_updated", executed=True,
                       action=action.definition.name, note_title=note.title,
                       note_status=note.status, reference_kind="note",
                       reference_id=note.id, reference_label=note.title)

    async def _execute_list_create(self, user: User, action: ExecutableAction,
                                   world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, ListCreateArgs)
        value = await self._lists.create_list(
            user, ListCreate(title=action.arguments.title)
        )
        return Outcome(kind="list_created", executed=True,
                       action=action.definition.name, list_title=value.title,
                       reference_kind="list", reference_id=value.id,
                       reference_label=value.title)

    async def _execute_list_list(self, user: User, action: ExecutableAction,
                                 world: WorldView, timezone_name: str | None) -> Outcome:
        values = await self._lists.list_lists(user)
        single = values[0] if len(values) == 1 else None
        return Outcome(kind="list_list", executed=True,
                       action=action.definition.name,
                       list_titles=tuple(value.title for value in values),
                       reference_kind="list" if single else None,
                       reference_id=single.id if single else None,
                       reference_label=single.title if single else None)

    async def _execute_list_add_item(self, user: User, action: ExecutableAction,
                                     world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, ListAddItemArgs)
        assert action.grounding.list_id is not None
        item = await self._lists.create_item(
            user, action.grounding.list_id,
            ListItemCreate(content=action.arguments.content),
        )
        return Outcome(kind="list_item_added", executed=True,
                       action=action.definition.name,
                       list_title=action.grounding.list_title,
                       list_item_content=item.content, reference_kind="list",
                       reference_id=action.grounding.list_id,
                       reference_label=action.grounding.list_title)

    async def _execute_list_complete_item(self, user: User, action: ExecutableAction,
                                          world: WorldView, timezone_name: str | None) -> Outcome:
        assert isinstance(action.arguments, ListCompleteItemArgs)
        assert action.grounding.list_id is not None
        assert action.grounding.list_item_id is not None
        item = await self._lists.update_item(
            user, action.grounding.list_id, action.grounding.list_item_id,
            ListItemUpdate(status="complete"),
        )
        return Outcome(kind="list_item_completed", executed=True,
                       action=action.definition.name,
                       list_title=action.grounding.list_title,
                       list_item_content=item.content, reference_kind="list",
                       reference_id=action.grounding.list_id,
                       reference_label=action.grounding.list_title)

    async def _execute_list_archive(self, user: User, action: ExecutableAction,
                                    world: WorldView, timezone_name: str | None) -> Outcome:
        assert action.grounding.list_id is not None
        value = await self._lists.update_list(
            user, action.grounding.list_id, ListUpdate(status="archived")
        )
        return Outcome(kind="list_archived", executed=True,
                       action=action.definition.name, list_title=value.title,
                       reference_kind="list", reference_id=value.id,
                       reference_label=value.title)

    def _resolved_action_from_proposal(
        self, proposal: ActionProposal, world: WorldView
    ) -> ResolvedAction:
        definition = registry.definition(proposal.action)
        if definition.grounder.value != proposal.action.partition(".")[0]:
            raise UnknownActionError(proposal.action)
        if (
            definition.reference is ReferencePolicy.NONE
            and proposal.reference is not None
        ):
            raise CompletionTargetNotFoundError(proposal.reference)
        if (
            definition.reference is ReferencePolicy.REQUIRED
            and not (proposal.reference or "").strip()
        ):
            raise CompletionTargetNotFoundError(
                f"that {definition.reference_kind or 'action'}"
            )
        try:
            typed_arguments = registry.parse_arguments(
                proposal.action, proposal.arguments
            )
        except registry.ActionArgumentsError as exc:
            raise CompletionTargetNotFoundError(
                proposal.reference or "that action"
            ) from exc
        arguments = typed_arguments.model_dump(exclude_none=True)
        if (
            proposal.action != registry.ACTIVITY_RECALL
            and proposal.recall_window is not None
        ):
            raise CompletionTargetNotFoundError("that action")

        if proposal.action == registry.TASK_UPDATE:
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
            if proposal.reference:
                raise CompletionTargetNotFoundError("activity recall")
            return ResolvedAction(
                action=registry.ACTIVITY_RECALL,
                recall_window=proposal.recall_window,
            )

        if proposal.action == registry.PROJECT_LIST:
            if proposal.reference:
                raise CompletionTargetNotFoundError("projects")
            return ResolvedAction(action=registry.PROJECT_LIST)

        if proposal.action == registry.PROJECT_CREATE:
            if proposal.reference:
                raise CompletionTargetNotFoundError("that project")
            name = arguments.get("name")
            return ResolvedAction(
                action=registry.PROJECT_CREATE,
                project_name=name,
            )

        if proposal.action == registry.TASK_CREATE:
            title = arguments.get("title")
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
            if proposal.reference:
                raise CompletionTargetNotFoundError("that reminder")
            title = arguments.get("title")
            when = arguments.get("when")
            return ResolvedAction(
                action=registry.REMINDER_CREATE,
                reminder_title=title,
                reminder_when=when,
            )

        if proposal.action == registry.REMINDER_LIST:
            if proposal.reference:
                raise CompletionTargetNotFoundError("reminders")
            return ResolvedAction(action=registry.REMINDER_LIST)

        if proposal.action in {
            registry.REMINDER_COMPLETE,
            registry.REMINDER_CANCEL,
        }:
            reminder = self._resolve_reminder_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=proposal.action,
                reminder_id=reminder.reminder_id,
                reminder_title=reminder.title,
            )

        if proposal.action == registry.NOTIFICATION_LIST:
            if proposal.reference:
                raise CompletionTargetNotFoundError("notifications")
            status = arguments.get("status")
            return ResolvedAction(
                action=registry.NOTIFICATION_LIST,
                notification_status="unread" if status == "unread" else None,
            )

        if proposal.action in {
            registry.NOTIFICATION_READ,
            registry.NOTIFICATION_DISMISS,
        }:
            notification = self._resolve_notification_reference(
                proposal.reference, world
            )
            return ResolvedAction(
                action=proposal.action,
                notification_id=notification.notification_id,
            )

        if proposal.action == registry.MEMORY_REMEMBER:
            return ResolvedAction(action=proposal.action, memory_kind=arguments["kind"],
                                  memory_subject=arguments["subject"], memory_content=arguments["content"])
        if proposal.action == registry.MEMORY_LIST:
            return ResolvedAction(action=proposal.action)
        if proposal.action in {registry.MEMORY_UPDATE, registry.MEMORY_FORGET}:
            memory = self._resolve_memory_reference(proposal.reference, world)
            return ResolvedAction(action=proposal.action, memory_id=memory.memory_id,
                                  memory_reference_subject=memory.subject, memory_subject=arguments.get("subject"),
                                  memory_kind=arguments.get("kind"), memory_content=arguments.get("content"))
        if proposal.action == registry.NOTE_CREATE:
            if proposal.reference:
                raise CompletionTargetNotFoundError("that note")
            title = arguments.get("title")
            return ResolvedAction(
                action=registry.NOTE_CREATE,
                note_title=title,
                note_content=arguments.get("content", ""),
            )

        if proposal.action == registry.NOTE_LIST:
            if proposal.reference:
                raise CompletionTargetNotFoundError("notes")
            status = arguments.get("status")
            return ResolvedAction(
                action=registry.NOTE_LIST,
                note_status="archived" if status == "archived" else "active",
            )

        if proposal.action in {registry.NOTE_UPDATE, registry.NOTE_ARCHIVE}:
            note = self._resolve_note_reference(proposal.reference, world)
            return ResolvedAction(
                action=proposal.action,
                note_id=note.note_id,
                note_title=arguments.get("title"),
                note_content=arguments.get("content"),
            )

        if proposal.action == registry.LIST_CREATE:
            if proposal.reference:
                raise CompletionTargetNotFoundError("that list")
            return ResolvedAction(action=proposal.action, list_title=arguments["title"])
        if proposal.action == registry.LIST_LIST:
            if proposal.reference:
                raise CompletionTargetNotFoundError("lists")
            return ResolvedAction(action=proposal.action)
        if proposal.action in {registry.LIST_ADD_ITEM, registry.LIST_ARCHIVE}:
            value = self._resolve_list_reference(proposal.reference, world)
            if proposal.action == registry.LIST_ADD_ITEM:
                content = arguments["content"]
            else:
                content = None
            return ResolvedAction(action=proposal.action, list_id=value.list_id,
                                  list_title=value.title, list_item_content=content)
        if proposal.action == registry.LIST_COMPLETE_ITEM:
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
            ctx = self._durable_reference("task")
            if ctx is not None and ctx.entity_id is not None:
                matches = [
                    task for task in world.tasks
                    if task.task_id == ctx.entity_id
                    and (not require_active or task.status == "active")
                ]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ctx.display_text)

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
        if target in self._PRONOUN_REFS:
            ref = self._durable_reference("project")
            if ref is not None and ref.entity_id is not None:
                matches = [p for p in world.projects if p.project_id == ref.entity_id]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
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

    def _resolve_context_project(self, world: WorldView) -> ProjectRef:
        ref = self._durable_reference("project")
        if ref is None or ref.entity_id is None:
            raise CompletionTargetNotFoundError("a project")
        matches = [p for p in world.projects if p.project_id == ref.entity_id]
        if len(matches) == 1:
            return matches[0]
        raise CompletionTargetNotFoundError(ref.display_text)

    def _resolve_reminder_reference(
        self, reference: str | None, world: WorldView
    ) -> ReminderRef:
        target = (reference or "").strip().lower()
        if target in self._PRONOUN_REFS | {"that reminder", "the reminder"}:
            ref = self._durable_reference("reminder")
            if ref is not None and ref.entity_id is not None:
                matches = [
                    reminder for reminder in world.reminders
                    if reminder.reminder_id == ref.entity_id
                    and reminder.status in {"scheduled", "due"}
                ]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
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
        if target in self._PRONOUN_REFS | {"that notification"}:
            ref = self._durable_reference("notification")
            if ref is not None and ref.entity_id is not None:
                matches = [
                    item for item in world.notifications
                    if item.notification_id == ref.entity_id
                    and item.status != "dismissed"
                ]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
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

    async def _load_memory_world(self, user: User, world: WorldView | None = None) -> WorldView:
        # This is only invoked after an explicit memory reference action/plan.
        values = await self._memories.list_memories(user, status="active")
        return replace(world or WorldView(projects=(), tasks=()), memories=tuple(
            MemoryRef(m.id, m.subject, m.kind, m.status) for m in values
        ))

    def _resolve_memory_reference(self, reference: str | None, world: WorldView) -> MemoryRef:
        target = (reference or "").strip().casefold()
        try:
            uuid.UUID(target)
        except ValueError:
            pass
        else:
            raise CompletionTargetNotFoundError("a memory subject")
        if target in self._PRONOUN_REFS | {"that memory", "the memory"}:
            ref = self._durable_reference("memory")
            if ref is not None:
                matches = [m for m in world.memories if m.memory_id == ref.entity_id and m.status == "active"]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
        active = [m for m in world.memories if m.status == "active"]
        exact = [m for m in active if m.subject.casefold() == target]
        matches = exact or [m for m in active if target and (target in m.subject.casefold() or m.subject.casefold() in target)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError([m.subject for m in matches])
        raise CompletionTargetNotFoundError(target or "that memory")

    def _resolve_note_reference(
        self, reference: str | None, world: WorldView
    ) -> NoteRef:
        target = (reference or "").strip().lower()
        if target in self._PRONOUN_REFS | {"that note"}:
            ref = self._durable_reference("note")
            if ref is not None and ref.entity_id is not None:
                matches = [
                    note for note in world.notes
                    if note.note_id == ref.entity_id and note.status == "active"
                ]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
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
        if target in self._PRONOUN_REFS | {"that list"}:
            ref = self._durable_reference("list")
            if ref is not None and ref.entity_id is not None:
                matches = [
                    value for value in world.lists
                    if value.list_id == ref.entity_id and value.status == "active"
                ]
                if len(matches) == 1:
                    return matches[0]
                raise CompletionTargetNotFoundError(ref.display_text)
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

    @staticmethod
    def _action_requires_world(action: str) -> bool:
        return registry.definition(action).requires_world

    @staticmethod
    def _live_information_category(message: str) -> str | None:
        for category, pattern in _LIVE_INFORMATION_PATTERNS:
            if pattern.search(message):
                return category
        return None

    async def _route_decision(self, message: str) -> RouteDecision | None:
        if (
            self._decision_provider is None
            or not self._decision_policy.route_enabled
        ):
            return None
        try:
            decision = await self._decision_provider.classify_route(message)
        except DecisionProviderError as exc:
            logger.info(
                "typesafe_decision_fallback",
                extra={
                    "decision_type": "route",
                    "provider_error": type(exc).__name__,
                    "fallback_used": True,
                },
            )
            return None
        if decision.confidence < self._decision_policy.route_min_confidence:
            logger.info(
                "typesafe_low_confidence",
                extra={
                    "decision_type": "route",
                    "confidence": decision.confidence,
                    "fallback_used": True,
                },
            )
            return None
        return decision

    async def _apply_route_decision(
        self,
        current_user: User,
        decision: RouteDecision | None,
        turn_language: str,
    ) -> WorldView | Outcome | None:
        if decision is None:
            return None
        if decision.kind is RouteKind.PERSONAL_CONTEXT:
            # Routing alone cannot describe a safe retrieval scope. The first
            # understanding pass must supply the typed query and scopes.
            return None
        if decision.kind is RouteKind.LIVE:
            return Outcome(
                kind="conversation",
                executed=False,
                reply=live_information_reply("live information", turn_language),
            )
        if decision.kind is RouteKind.CLARIFICATION:
            return Outcome(
                kind="ambiguous",
                executed=False,
                reply="Could you clarify what you want me to do?",
            )
        if decision.kind is RouteKind.UNSUPPORTED:
            return Outcome(
                kind="unsupported",
                executed=False,
                reply="I can't safely handle that request yet.",
            )
        # Single action, multi-step plan, and ordinary conversation still go
        # through the existing understanding provider. The classification has
        # no authority to propose or execute an action.
        return None

    @staticmethod
    def _route_decision_may_help(message: str) -> bool:
        """Avoid adding a decision call to ordinary provider-backed chat."""

        return bool(
            re.search(
                r"\b(?:my|this|that|it|task|project|reminder|notification|note|"
                r"list|create|make|add|update|complete|archive|dismiss|cancel|"
                r"current|latest|today|weather|news|price|score)\b",
                message,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _looks_like_multi_action(message: str) -> bool:
        if not re.search(r"\b(?:and|then|also|after that)\b", message, re.IGNORECASE):
            return False
        verbs = re.findall(
            r"\b(?:create|make|start|add|update|complete|finish|mark|check|archive|"
            r"dismiss|cancel|remind|list|show|remember|store|save|forget)\b",
            message,
            re.IGNORECASE,
        )
        return len(verbs) >= 2

    def _context_payload(
        self,
        current_user: User,
        *,
        include_personal_context: bool = True,
        include_general_context: bool = False,
    ) -> dict | None:
        payload: dict = {}
        if include_personal_context:
            task_ref = self._durable_reference("task")
            project_ref = self._durable_reference("project")
            if task_ref is not None:
                payload["last_grounded_entity"] = {
                    "kind": "task",
                    "title": task_ref.display_text,
                    **task_ref.metadata,
                }
            if project_ref is not None:
                payload["last_grounded_project"] = {
                    "kind": "project",
                    "name": project_ref.display_text,
                }
        elif include_general_context:
            prior = self._durable_reference("prior_result")
            if prior is not None:
                payload["previous_general_turn"] = {
                    "message": str(prior.metadata.get("message", "")),
                    "reply": prior.display_text,
                    "language": str(prior.metadata.get("language", "en")),
                }
            turns: tuple[RecentTurn, ...] = getattr(
                self, "_current_recent_turns", ()
            )
            if turns:
                payload["recent_turns"] = [
                    {
                        "role": turn.role,
                        "content": turn.content,
                        "language": turn.language,
                    }
                    for turn in turns
                ]
        return payload or None

    def _durable_reference(self, kind: str) -> DurableReference | None:
        references: dict[str, DurableReference] = getattr(
            self, "_current_references", {}
        )
        return references.get(kind)

    def _fresh_live_reference(self, kind: str) -> DurableReference | None:
        reference = self._durable_reference(kind)
        if reference is None:
            return None
        updated_at = reference.updated_at
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = ensure_utc(self._clock.now()) - updated_at.astimezone(timezone.utc)
        return reference if age <= self._LIVE_REFERENCE_MAX_AGE else None

    async def _remember_outcome(self, outcome: Outcome) -> None:
        if not outcome.reference_kind or not outcome.reference_label:
            return
        if outcome.reference_kind == "memory" and outcome.memory_status == "forgotten":
            return
        await self._context_store.set_reference(
            self._current_thread_id,
            kind=outcome.reference_kind,
            entity_id=outcome.reference_id,
            display_text=outcome.reference_label,
            metadata=outcome.reference_metadata,
        )
        if outcome.reference_kind == "task" and outcome.reference_metadata:
            project_id = outcome.reference_metadata.get("project_id")
            project_name = outcome.reference_metadata.get("project")
            if project_id and project_name:
                await self._context_store.set_reference(
                    self._current_thread_id,
                    kind="project",
                    entity_id=uuid.UUID(project_id),
                    display_text=project_name,
                )
        self._current_references = await self._context_store.references(
            self._current_thread_id
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
        if activity.entity_type == "memory":
            subject = payload.get("subject")
            return subject if isinstance(subject, str) else None
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
