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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

from app.projects.service import ProjectsService
from app.tasks.service import TasksService
from app.tasks.schemas import TaskUpdate

from app.conversation import registry
from app.conversation.exceptions import (
    AmbiguousReferenceError,
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


class ConversationService:
    """Orchestrates one conversational turn over the peer capabilities."""

    def __init__(
        self, session: AsyncSession, resolver: Resolver | None = None
    ) -> None:
        # One shared session across every composed service, so a dispatched
        # mutation and its Activity event commit atomically.
        self._session = session
        self._projects = ProjectsService(session)
        self._tasks = TasksService(session)
        # Resolver is injectable so tests can pin behavior; defaults to the
        # deterministic v1 brain.
        self._resolver: Resolver = resolver or HardcodedResolver()

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
                    )
                )
        return WorldView(projects=project_refs, tasks=tuple(task_refs))

    async def handle(
        self, current_user: User, message: str
    ) -> ConversationResponse:
        world = await self._build_world(current_user)

        # Resolver proposes; it executes nothing.
        try:
            action = self._resolver.resolve(message, world)
        except NoMatchError:
            return ConversationResponse(
                executed=False,
                reply="I couldn't find anything matching that.",
            )
        except AmbiguousReferenceError as exc:
            listed = "\n".join(f"- {c}" for c in exc.candidates)
            return ConversationResponse(
                executed=False,
                reply=f"I found several matches:\n{listed}\nWhich one?",
            )

        # Trust boundary: the proposed action must be in the closed registry.
        if not registry.is_allowed(action.action):
            raise UnknownActionError(action.action)

        return await self._dispatch(current_user, action)

    async def _dispatch(
        self, current_user: User, action: ResolvedAction
    ) -> ConversationResponse:
        if action.action == registry.PROJECT_LIST:
            projects = await self._projects.list_projects(current_user)
            if not projects:
                return ConversationResponse(
                    executed=True,
                    action=action.action,
                    reply="You have no projects yet.",
                )
            names = ", ".join(p.name for p in projects)
            return ConversationResponse(
                executed=True,
                action=action.action,
                reply=f"You have {len(projects)} project(s): {names}.",
            )

        if action.action == registry.TASK_LIST:
            assert action.project_id is not None
            tasks = await self._tasks.list_tasks(
                current_user, action.project_id
            )
            if not tasks:
                return ConversationResponse(
                    executed=True,
                    action=action.action,
                    reply="That project has no tasks.",
                )
            active = [t for t in tasks if t.status == "active"]
            return ConversationResponse(
                executed=True,
                action=action.action,
                reply=(
                    f"That project has {len(tasks)} task(s), "
                    f"{len(active)} active."
                ),
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
            return ConversationResponse(
                executed=True,
                action=action.action,
                reply=f"Marked '{task.title}' as {task.status}.",
            )

        # Registry membership was checked upstream; reaching here is a bug.
        raise UnknownActionError(action.action)
