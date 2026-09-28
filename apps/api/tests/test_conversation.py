"""Conversation orchestration-layer tests: the deterministic dispatch spine.

Hermetic, mirroring tests/test_activity.py: in-memory SQLite (aiosqlite),
schema from Base.metadata, Platform get_session overridden, users created via
/identity/users then verified directly.

Conversation owns no state. These tests drive it two ways:

* **HTTP integration** through POST /conversation for the happy path and the
  honest non-execution edges (no match, ambiguity, verb-gating). Real
  projects and tasks are created via the domain endpoints; the resulting
  Activity ledger is asserted via GET /activity, proving the dispatched
  mutation and its task.completed event committed atomically on the shared
  session.
* **Direct unit** of ConversationService with an injected stub resolver for
  the unknown-action edge, which cannot arise through HTTP (the HTTP path
  always uses the default HardcodedResolver). This exercises the trust
  boundary: a proposed action outside the closed registry is refused before
  any service is touched.
"""
from __future__ import annotations

import json
import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.activity import Activity
from app.models.reminder import Reminder
from app.models.scheduled_job import ScheduledJob
from app.notifications.schemas import NotificationCreate
from app.notifications.service import NotificationsService
from app.models.user import User
from app.models.conversation import (
    ConversationThread, ConversationTurnRecord, PendingConversationPlan,
)
from app.conversation.context import (
    MAX_STORED_TURNS,
    RECENT_TURN_LIMIT,
    ConversationContextStore,
)

import app.models  # noqa: F401  (populate Base.metadata before create_all)

from app.conversation import registry
from app.conversation.actions.registry import ActionArgumentsError
from app.conversation.service import ConversationService
from app.conversation.exceptions import UnknownActionError
from app.conversation.dependencies import get_understanding_provider
from app.conversation.openai_provider import OpenAIUnderstandingProvider
from app.conversation.resolver import ProjectRef, Resolver, WorldView
from app.conversation.schemas import ResolvedAction
from app.conversation.understanding import (
    ActionProposal,
    Clarification,
    ConversationTurn,
    PersonalContextRequest,
    PlanProposal,
    UNDERSTANDING_JSON_SCHEMA,
    UnderstandingProviderError,
    UnderstandingResult,
    Unsupported,
    parse_understanding_payload,
    safe_world_payload,
)
from app.conversation.plans.base import ProposedPlan, ProposedPlanStep
from app.conversation.plans.compiler import PlanCompiler
from app.conversation.plans.store import (
    PendingPlanConflictError,
    PendingPlanStore,
    PlanLifecycleTransitionError,
)
from app.live.dependencies import get_live_intelligence_service
from app.live.service import LiveLookupResult
from app.live.schemas import SourceMetadata, WeatherReport
from app.intelligence.decision import (
    ConfirmationDecision,
    ConfirmationKind,
    DecisionPolicy,
    DecisionProviderError,
    PlanVerificationDecision,
    RouteDecision,
    RouteKind,
)

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


class _FakeUnderstandingProvider:
    def __init__(
        self, result: object | Exception | list[object | Exception]
    ) -> None:
        self._results = result if isinstance(result, list) else [result]
        self.calls: list[dict[str, object]] = []

    async def understand(self, **_: object) -> object:
        self.calls.append(_)
        result = self._results[min(len(self.calls) - 1, len(self._results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeDecisionProvider:
    def __init__(
        self,
        *,
        route: RouteDecision | Exception | None = None,
        confirmation: ConfirmationDecision | Exception | None = None,
        verification: PlanVerificationDecision | Exception | None = None,
    ) -> None:
        self.route_result = route or RouteDecision(
            RouteKind.CONVERSATION, 0.95, {"conversation": 0.95}
        )
        self.confirmation_result = confirmation or ConfirmationDecision(
            ConfirmationKind.UNCERTAIN, 0.95, {"uncertain": 0.95}
        )
        self.verification_result = verification or PlanVerificationDecision(
            unrequested_action_probability=0.03,
            omitted_action_probability=0.02,
            excessive_mutation_probability=0.01,
            faithful_probability=0.95,
        )
        self.route_calls: list[str] = []
        self.confirmation_calls: list[tuple[str, str]] = []
        self.verification_calls: list[tuple[str, object]] = []

    async def classify_route(self, message: str) -> RouteDecision:
        self.route_calls.append(message)
        if isinstance(self.route_result, Exception):
            raise self.route_result
        return self.route_result

    async def classify_confirmation(
        self, response: str, plan_summary: str
    ) -> ConfirmationDecision:
        self.confirmation_calls.append((response, plan_summary))
        if isinstance(self.confirmation_result, Exception):
            raise self.confirmation_result
        return self.confirmation_result

    async def verify_plan(
        self, request: str, plan: object
    ) -> PlanVerificationDecision:
        self.verification_calls.append((request, plan))
        if isinstance(self.verification_result, Exception):
            raise self.verification_result
        return self.verification_result


def _use_fake_provider(
    result: object | Exception | list[object | Exception],
) -> _FakeUnderstandingProvider:
    provider = _FakeUnderstandingProvider(result)
    fastapi_app.dependency_overrides[get_understanding_provider] = (
        lambda: provider
    )
    return provider


class _FakeLiveService:
    def __init__(self, result: LiveLookupResult | None) -> None:
        self._result = result
        self.resolved: list[str] = []
        self.executed: list[object] = []

    def resolve(self, message: str) -> object | None:
        self.resolved.append(message)
        if self._result is None:
            return None
        from app.live.intent import resolve_live_intent

        return resolve_live_intent(message)

    async def execute(self, intent: object) -> LiveLookupResult:
        self.executed.append(intent)
        assert self._result is not None
        return self._result


def _use_fake_live_service(result: LiveLookupResult | None) -> _FakeLiveService:
    service = _FakeLiveService(result)
    fastapi_app.dependency_overrides[get_live_intelligence_service] = (
        lambda: service
    )
    return service


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return type(
            "Response",
            (),
            {
                "output_text": json.dumps(
                    {
                        "kind": "conversation",
                        "action": None,
                        "reference": None,
                        "arguments": None,
                        "recall_window": None,
                        "reply": "Tokyo is the capital of Japan.",
                        "prompt": None,
                        "candidates": None,
                        "reason": None,
                    }
                )
            },
        )()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Normal tests are hermetic even when a developer's local .env enables
    # the optional live TypeSafe adapter. Decision-specific tests inject a
    # fake provider directly.
    monkeypatch.setenv("TYPESAFE_ENABLED", "false")


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = _override_get_session
    fastapi_app.dependency_overrides[get_live_intelligence_service] = lambda: None
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            yield ac, sessionmaker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        fastapi_app.dependency_overrides.pop(get_understanding_provider, None)
        fastapi_app.dependency_overrides.pop(get_live_intelligence_service, None)
        await engine.dispose()


async def _make_login_ready_user(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
    *,
    email: str | None = None,
) -> str:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/identity/users",
        json={"email": email, "password": PASSWORD, "full_name": "Ada"},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["id"]
    async with sessionmaker() as s:
        await s.execute(
            update(User)
            .where(User.id == uuid.UUID(uid))
            .values(is_verified=True, is_active=True)
        )
        await s.commit()
    return email


async def _auth_headers(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
) -> tuple[dict[str, str], str]:
    email = await _make_login_ready_user(client, sessionmaker)
    resp = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    access = resp.json()["access_token"]
    async with sessionmaker() as s:
        row = (
            await s.execute(
                User.__table__.select().where(User.email == email)
            )
        ).first()
    return {"Authorization": f"Bearer {access}"}, str(row.id)


async def _make_project(
    client: httpx.AsyncClient, headers: dict[str, str], name: str = "P"
) -> str:
    resp = await client.post("/projects", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    project_id: str,
    title: str = "T",
    status: str | None = None,
) -> dict:
    body: dict = {"title": title}
    if status is not None:
        body["status"] = status
    resp = await client.post(
        f"/projects/{project_id}/tasks", json=body, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get_activity(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> list[dict]:
    resp = await client.get("/activity", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_task(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    project_id: str,
    task_id: str,
) -> dict:
    resp = await client.get(
        f"/projects/{project_id}/tasks/{task_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_notification(
    sessionmaker: async_sessionmaker,
    user_id: str,
    title: str,
    body: str | None = None,
) -> str:
    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        item = await NotificationsService(session).create_notification(
            user,
            NotificationCreate(
                type="reminder",
                title=title,
                body=body or title,
                source_type="reminder",
                source_id=uuid.uuid4(),
            ),
        )
        return str(item.id)


async def _set_activity_created_at(
    sessionmaker: async_sessionmaker,
    activity_id: str,
    created_at: datetime,
) -> None:
    async with sessionmaker() as s:
        await s.execute(
            update(Activity)
            .where(Activity.id == uuid.UUID(activity_id))
            .values(created_at=created_at)
        )
        await s.commit()


async def _record_activity(
    sessionmaker: async_sessionmaker,
    *,
    user_id: str,
    event_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    created_at: datetime,
    payload: dict | None = None,
) -> str:
    async with sessionmaker() as s:
        activity = Activity(
            user_id=uuid.UUID(user_id),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            created_at=created_at,
        )
        s.add(activity)
        await s.commit()
        return str(activity.id)


def _local_datetime_for_day_offset(
    timezone_name: str,
    day_offset: int,
    at_time: time,
) -> datetime:
    user_tz = ZoneInfo(timezone_name)
    target_date = (
        datetime.now(timezone.utc).astimezone(user_tz).date()
        + timedelta(days=day_offset)
    )
    return datetime.combine(target_date, at_time, tzinfo=user_tz).astimezone(
        timezone.utc
    )


# --------------------------------------------------------------------------
# Reminders: deterministic language through authoritative scheduling.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reminder_via_conversation_persists_job_and_activity(
    ctx,
) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    async with sessionmaker() as session:
        await session.execute(
            update(User)
            .where(User.id == uuid.UUID(user_id))
            .values(timezone="Asia/Kolkata")
        )
        await session.commit()

    response = await client.post(
        "/conversation",
        json={"message": "Remind me tomorrow at 6 PM to call Ramesh"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is True
    assert body["action"] == "reminder.create"
    assert "call Ramesh" in body["reply"]
    assert "6:00 PM" in body["reply"]

    expected = _local_datetime_for_day_offset(
        "Asia/Kolkata", 1, time(18, 0)
    )
    async with sessionmaker() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.user_id == uuid.UUID(user_id))
            )
        ).scalar_one()
        job = await session.get(ScheduledJob, reminder.scheduled_job_id)
        assert reminder.title == "call Ramesh"
        assert reminder.timezone == "Asia/Kolkata"
        assert reminder.due_at.replace(tzinfo=timezone.utc) == expected
        assert job is not None and job.job_type == "reminder.due"
        activity = (
            await session.execute(
                select(Activity).where(
                    Activity.entity_id == reminder.id,
                    Activity.event_type == "reminder.created",
                )
            )
        ).scalar_one()
        assert activity.payload["title"] == "call Ramesh"


@pytest.mark.asyncio
async def test_list_complete_and_cancel_reminders_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    for title in ("call Ramesh", "submit expenses"):
        created = await client.post(
            "/conversation",
            json={"message": f"Remind me tomorrow at 6 PM to {title}"},
            headers=headers,
        )
        assert created.json()["executed"] is True

    listed = await client.post(
        "/conversation",
        json={"message": "What reminders do I have?"},
        headers=headers,
    )
    assert listed.json()["action"] == "reminder.list"
    assert "call Ramesh" in listed.json()["reply"]
    assert "submit expenses" in listed.json()["reply"]

    completed = await client.post(
        "/conversation",
        json={"message": "Complete my call Ramesh reminder"},
        headers=headers,
    )
    cancelled = await client.post(
        "/conversation",
        json={"message": "Cancel my submit expenses reminder"},
        headers=headers,
    )
    assert completed.json()["action"] == "reminder.complete"
    assert cancelled.json()["action"] == "reminder.cancel"

    reminders = await client.get("/reminders", headers=headers)
    statuses = {item["title"]: item["status"] for item in reminders.json()}
    assert statuses == {
        "call Ramesh": "completed",
        "submit expenses": "cancelled",
    }


@pytest.mark.asyncio
async def test_reminder_completion_wins_over_same_named_task(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers)
    task = await _make_task(client, headers, project_id, title="call Ramesh")
    await client.post(
        "/conversation",
        json={"message": "Remind me tomorrow at 6 PM to call Ramesh"},
        headers=headers,
    )

    response = await client.post(
        "/conversation",
        json={"message": "Complete my call Ramesh reminder"},
        headers=headers,
    )

    assert response.json()["action"] == "reminder.complete"
    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_ambiguous_reminder_reference_fails_closed(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    for title in ("call Ramesh", "call Ramesh about launch"):
        await client.post(
            "/conversation",
            json={"message": f"Remind me tomorrow at 6 PM to {title}"},
            headers=headers,
        )

    response = await client.post(
        "/conversation",
        json={"message": "Cancel my call Ramesh reminder"},
        headers=headers,
    )

    assert response.json()["executed"] is False
    reminders = await client.get("/reminders", headers=headers)
    assert {item["status"] for item in reminders.json()} == {"scheduled"}


@pytest.mark.asyncio
async def test_provider_reminder_proposal_stays_inside_registry(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="reminder.create",
            arguments={"title": "call Ramesh", "when": "tomorrow at 6 PM"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Please make sure I call Ramesh tomorrow evening"},
        headers=headers,
    )

    assert response.json()["executed"] is True
    assert response.json()["action"] == "reminder.create"
    reminders = await client.get("/reminders", headers=headers)
    assert [item["title"] for item in reminders.json()] == ["call Ramesh"]


# --------------------------------------------------------------------------
# Notifications: closed, ownership-scoped acknowledgement actions.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_and_read_notification_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    notification_id = await _make_notification(
        sessionmaker, user_id, "Call Ramesh", "It is time to call Ramesh."
    )

    listed = await client.post(
        "/conversation",
        json={"message": "Show unread notifications"},
        headers=headers,
    )
    assert listed.json()["action"] == "notification.list"
    assert "Call Ramesh" in listed.json()["reply"]

    read = await client.post(
        "/conversation",
        json={"message": "Mark that notification as read"},
        headers=headers,
    )
    assert read.json()["executed"] is True
    assert read.json()["action"] == "notification.read"
    fetched = await client.get(
        f"/notifications/{notification_id}", headers=headers
    )
    assert fetched.json()["status"] == "read"
    assert fetched.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_dismiss_notification_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    notification_id = await _make_notification(
        sessionmaker, user_id, "Submit expenses"
    )

    response = await client.post(
        "/conversation",
        json={"message": "Dismiss that notification"},
        headers=headers,
    )

    assert response.json()["action"] == "notification.dismiss"
    fetched = await client.get(
        f"/notifications/{notification_id}", headers=headers
    )
    assert fetched.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_ambiguous_notification_reference_fails_closed(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    await _make_notification(sessionmaker, user_id, "First")
    await _make_notification(sessionmaker, user_id, "Second")

    response = await client.post(
        "/conversation",
        json={"message": "Dismiss that notification"},
        headers=headers,
    )

    assert response.json()["executed"] is False
    listed = await client.get("/notifications", headers=headers)
    assert {item["status"] for item in listed.json()} == {"unread"}


@pytest.mark.asyncio
async def test_conversation_cannot_access_another_users_notification(ctx) -> None:
    client, sessionmaker = ctx
    mine, _ = await _auth_headers(client, sessionmaker)
    _, other_id = await _auth_headers(client, sessionmaker)
    await _make_notification(sessionmaker, other_id, "Private alert")

    response = await client.post(
        "/conversation",
        json={"message": "Dismiss the private alert notification"},
        headers=mine,
    )

    assert response.json()["executed"] is False


@pytest.mark.asyncio
async def test_provider_notification_action_resolves_owned_title(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="notification.dismiss",
            reference="Call Ramesh",
        )
    )
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    notification_id = await _make_notification(
        sessionmaker, user_id, "Call Ramesh"
    )

    response = await client.post(
        "/conversation",
        json={"message": "Clear the alert about Ramesh"},
        headers=headers,
    )

    assert response.json()["action"] == "notification.dismiss"
    fetched = await client.get(
        f"/notifications/{notification_id}", headers=headers
    )
    assert fetched.json()["status"] == "dismissed"


# --------------------------------------------------------------------------
# Notes: durable user artifacts through closed grounded actions.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_notes_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    created = await client.post(
        "/conversation",
        json={"message": "Make a note that the investor call moved to Friday"},
        headers=headers,
    )
    assert created.json()["executed"] is True
    assert created.json()["action"] == "note.create"

    named = await client.post(
        "/conversation",
        json={"message": "Create a note called launch ideas"},
        headers=headers,
    )
    assert named.json()["action"] == "note.create"

    listed = await client.post(
        "/conversation",
        json={"message": "Show my notes"},
        headers=headers,
    )
    assert listed.json()["action"] == "note.list"
    assert "launch ideas" in listed.json()["reply"]
    notes = await client.get("/notes", headers=headers)
    by_title = {item["title"]: item["content"] for item in notes.json()}
    assert by_title["the investor call moved to Friday"] == (
        "the investor call moved to Friday"
    )
    assert by_title["launch ideas"] == ""


@pytest.mark.asyncio
async def test_update_and_archive_note_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/notes",
        json={"title": "Launch ideas", "content": "Initial"},
        headers=headers,
    )

    updated = await client.post(
        "/conversation",
        json={"message": "Update the launch ideas note with Invite Ramesh"},
        headers=headers,
    )
    assert updated.json()["action"] == "note.update"
    fetched = await client.get(f"/notes/{created.json()['id']}", headers=headers)
    assert fetched.json()["content"] == "Invite Ramesh"

    archived = await client.post(
        "/conversation",
        json={"message": "Archive the launch ideas note"},
        headers=headers,
    )
    assert archived.json()["action"] == "note.archive"
    fetched = await client.get(f"/notes/{created.json()['id']}", headers=headers)
    assert fetched.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_ambiguous_note_title_fails_closed(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    for title in ("Launch ideas", "Launch ideas for Europe"):
        await client.post("/notes", json={"title": title}, headers=headers)

    response = await client.post(
        "/conversation",
        json={"message": "Archive the launch ideas note"},
        headers=headers,
    )
    assert response.json()["executed"] is False
    listed = await client.get("/notes", headers=headers)
    assert {item["status"] for item in listed.json()} == {"active"}


@pytest.mark.asyncio
async def test_conversation_note_resolution_is_owner_scoped(ctx) -> None:
    client, sessionmaker = ctx
    mine, _ = await _auth_headers(client, sessionmaker)
    theirs, _ = await _auth_headers(client, sessionmaker)
    await client.post("/notes", json={"title": "Private plan"}, headers=theirs)

    response = await client.post(
        "/conversation",
        json={"message": "Archive the private plan note"},
        headers=mine,
    )
    assert response.json()["executed"] is False


@pytest.mark.asyncio
async def test_provider_note_update_validates_reference_and_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="note.update",
            reference="Launch ideas",
            arguments={"content": "Invite design partners"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/notes", json={"title": "Launch ideas"}, headers=headers
    )

    response = await client.post(
        "/conversation",
        json={"message": "Add the design partners to my launch thought"},
        headers=headers,
    )
    assert response.json()["action"] == "note.update"
    fetched = await client.get(f"/notes/{created.json()['id']}", headers=headers)
    assert fetched.json()["content"] == "Invite design partners"


@pytest.mark.asyncio
async def test_provider_note_action_rejects_unregistered_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="note.update",
            reference="Launch ideas",
            arguments={"user_id": str(uuid.uuid4()), "content": "stolen"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/notes",
        json={"title": "Launch ideas", "content": "original"},
        headers=headers,
    )

    response = await client.post(
        "/conversation",
        json={"message": "Change my launch note"},
        headers=headers,
    )
    assert response.json()["executed"] is False
    fetched = await client.get(f"/notes/{created.json()['id']}", headers=headers)
    assert fetched.json()["content"] == "original"


# --------------------------------------------------------------------------
# Lists: lightweight collections remain distinct from Tasks.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversation_happy_path(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/conversation", json={"message": "Create a grocery list"}, headers=headers
    )
    assert created.json()["action"] == "list.create"
    listed = await client.post(
        "/conversation", json={"message": "Show my lists"}, headers=headers
    )
    assert listed.json()["action"] == "list.list"
    added = await client.post(
        "/conversation", json={"message": "Add milk to the grocery list"}, headers=headers
    )
    assert added.json()["action"] == "list.add_item"
    completed = await client.post(
        "/conversation",
        json={"message": "Mark milk complete on the grocery list"},
        headers=headers,
    )
    assert completed.json()["action"] == "list.complete_item"
    values = await client.get("/lists", headers=headers)
    list_id = values.json()[0]["id"]
    items = await client.get(f"/lists/{list_id}/items", headers=headers)
    assert items.json()[0]["status"] == "complete"
    archived = await client.post(
        "/conversation", json={"message": "Archive the grocery list"}, headers=headers
    )
    assert archived.json()["action"] == "list.archive"


@pytest.mark.asyncio
async def test_ambiguous_list_and_item_resolution_fail_closed(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    for title in ("Trip", "Trip packing"):
        await client.post("/lists", json={"title": title}, headers=headers)
    ambiguous_list = await client.post(
        "/conversation", json={"message": "Add passport to the trip list"}, headers=headers
    )
    assert ambiguous_list.json()["executed"] is False

    grocery = await client.post("/lists", json={"title": "Grocery"}, headers=headers)
    list_id = grocery.json()["id"]
    for content in ("milk", "milk chocolate"):
        await client.post(f"/lists/{list_id}/items", json={"content": content}, headers=headers)
    ambiguous_item = await client.post(
        "/conversation",
        json={"message": "Mark milk complete on the grocery list"},
        headers=headers,
    )
    assert ambiguous_item.json()["executed"] is False


@pytest.mark.asyncio
async def test_list_conversation_is_owner_scoped(ctx) -> None:
    client, sessionmaker = ctx
    mine, _ = await _auth_headers(client, sessionmaker)
    theirs, _ = await _auth_headers(client, sessionmaker)
    await client.post("/lists", json={"title": "Private"}, headers=theirs)
    response = await client.post(
        "/conversation", json={"message": "Archive the private list"}, headers=mine
    )
    assert response.json()["executed"] is False


@pytest.mark.asyncio
async def test_provider_list_action_validates_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action", action="list.add_item", reference="Grocery",
            arguments={"content": "milk"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    parent = await client.post("/lists", json={"title": "Grocery"}, headers=headers)
    response = await client.post(
        "/conversation", json={"message": "Put milk on groceries"}, headers=headers
    )
    assert response.json()["action"] == "list.add_item"
    items = await client.get(f"/lists/{parent.json()['id']}/items", headers=headers)
    assert [item["content"] for item in items.json()] == ["milk"]


@pytest.mark.asyncio
async def test_provider_list_action_rejects_extra_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action", action="list.add_item", reference="Grocery",
            arguments={"content": "milk", "position": "0"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    parent = await client.post("/lists", json={"title": "Grocery"}, headers=headers)
    response = await client.post(
        "/conversation", json={"message": "Put milk on groceries"}, headers=headers
    )
    assert response.json()["executed"] is False
    items = await client.get(f"/lists/{parent.json()['id']}/items", headers=headers)
    assert items.json() == []


# --------------------------------------------------------------------------
# Happy path: the proof loop.
# --------------------------------------------------------------------------


def test_understanding_schema_is_strict_responses_api_shape() -> None:
    properties = UNDERSTANDING_JSON_SCHEMA["properties"]

    assert UNDERSTANDING_JSON_SCHEMA["additionalProperties"] is False
    assert set(UNDERSTANDING_JSON_SCHEMA["required"]) == set(properties)
    assert "null" in properties["reply"]["type"]
    assert properties["reply"]["maxLength"] == 4000


def test_understanding_schema_allows_useful_bounded_general_answers() -> None:
    reply = "A useful explanation. " * 100
    result = parse_understanding_payload(
        {
            "kind": "conversation",
            "action": None,
            "reference": None,
            "arguments": None,
            "recall_window": None,
            "reply": reply,
            "prompt": None,
            "candidates": None,
            "reason": None,
        }
    )

    assert isinstance(result, ConversationTurn)
    assert result.reply == reply


def test_provider_allowed_actions_stay_synced_with_registry() -> None:
    payload = safe_world_payload(WorldView(projects=(), tasks=()))

    assert payload["allowed_actions"] == list(registry.ACTION_NAMES)


@pytest.mark.asyncio
async def test_create_project_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Create a project called Website Redesign"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is True
    assert body["action"] == "project.create"
    assert "Website Redesign" in body["reply"]
    assert "project.create" not in body["reply"]

    projects = await client.get("/projects", headers=headers)
    assert [project["name"] for project in projects.json()] == [
        "Website Redesign"
    ]
    activity = await _get_activity(client, headers)
    assert any(
        item["event_type"] == "project.created"
        and item["payload"]["name"] == "Website Redesign"
        for item in activity
    )


@pytest.mark.asyncio
async def test_create_task_via_conversation_resolves_project_name(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="TestDev")

    response = await client.post(
        "/conversation",
        json={"message": "Add a task to TestDev called Fix mobile navigation"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is True
    assert body["action"] == "task.create"
    assert "Fix mobile navigation" in body["reply"]
    assert "TestDev" in body["reply"]
    assert project_id not in body["reply"]

    tasks = await client.get(f"/projects/{project_id}/tasks", headers=headers)
    assert [task["title"] for task in tasks.json()] == [
        "Fix mobile navigation"
    ]
    activity = await _get_activity(client, headers)
    assert any(
        item["event_type"] == "task.created"
        and item["payload"]["title"] == "Fix mobile navigation"
        for item in activity
    )


@pytest.mark.asyncio
async def test_create_task_can_use_last_grounded_project(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    created_project = await client.post(
        "/conversation",
        json={"message": "Create a project called Atlas"},
        headers=headers,
    )
    assert created_project.json()["action"] == "project.create"

    created_task = await client.post(
        "/conversation",
        json={"message": "Add a task called Build landing page"},
        headers=headers,
    )

    assert created_task.json()["executed"] is True
    assert created_task.json()["action"] == "task.create"
    assert "Atlas" in created_task.json()["reply"]
    projects = await client.get("/projects", headers=headers)
    project_id = projects.json()[0]["id"]
    tasks = await client.get(f"/projects/{project_id}/tasks", headers=headers)
    assert [task["title"] for task in tasks.json()] == ["Build landing page"]


@pytest.mark.asyncio
async def test_create_task_ambiguous_project_name_fails_closed(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    first = await _make_project(client, headers, name="TestDev")
    second = await _make_project(client, headers, name="TestDev Mobile")

    response = await client.post(
        "/conversation",
        json={"message": "Create a task called Fix login under Test"},
        headers=headers,
    )

    assert response.json()["executed"] is False
    for project_id in (first, second):
        tasks = await client.get(f"/projects/{project_id}/tasks", headers=headers)
        assert tasks.json() == []


@pytest.mark.asyncio
async def test_provider_task_create_rejects_extra_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.create",
            reference="TestDev",
            arguments={
                "title": "Fix mobile navigation",
                "user_id": str(uuid.uuid4()),
            },
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="TestDev")

    response = await client.post(
        "/conversation",
        json={"message": "Add the mobile navigation fix"},
        headers=headers,
    )

    assert response.json()["executed"] is False
    tasks = await client.get(f"/projects/{project_id}/tasks", headers=headers)
    assert tasks.json() == []


@pytest.mark.parametrize("arguments", [{}, {"name": 42}])
@pytest.mark.asyncio
async def test_provider_project_create_rejects_missing_or_wrong_typed_arguments(
    ctx, arguments: dict[str, object]
) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="project.create",
            arguments=arguments,  # type: ignore[arg-type] - hostile provider payload
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Create a project"},
        headers=headers,
    )

    assert response.json()["executed"] is False
    projects = await client.get("/projects", headers=headers)
    assert projects.json() == []


@pytest.mark.asyncio
async def test_complete_task_via_conversation_emits_completed(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky Platform")
    task = await _make_task(
        client, headers, project_id, title="homepage copy"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "complete the homepage copy task"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.update"
    assert "complete" in body["reply"].lower()

    # The task really transitioned.
    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"
    assert fetched["completed_at"] is not None

    # And task.completed landed in the ledger — atomic emission through the
    # orchestrator on the shared session.
    activity = await _get_activity(client, headers)
    completed = [a for a in activity if a["event_type"] == "task.completed"]
    assert len(completed) == 1
    assert completed[0]["entity_id"] == task["id"]


@pytest.mark.parametrize(
    "message",
    [
        "Finish the homepage task",
        "Finish homepage",
        "Complete the homepage task",
        "Mark the homepage task complete",
        "Homepage is done",
        "I finished the homepage task",
        "Finished the homepage task",
        "Hey Rocky, finish the homepage task",
        "Hey OK finished the homepage task",
    ],
)
@pytest.mark.asyncio
async def test_completion_phrases_resolve_to_task_update(
    ctx, message: str
) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky Platform")
    task = await _make_task(client, headers, project_id, title="Homepage")

    resp = await client.post(
        "/conversation",
        json={"message": message},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.update"

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"
    activity = await _get_activity(client, headers)
    completed = [a for a in activity if a["event_type"] == "task.completed"]
    assert len(completed) == 1
    assert completed[0]["entity_id"] == task["id"]


@pytest.mark.asyncio
async def test_live_homepage_phrase_matches_task_title_containing_homepage(
    ctx,
) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="TestDev")
    task = await _make_task(
        client, headers, project_id, title="Create the homepage"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "Hey Rocky finish the homepage task"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.update"

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"


@pytest.mark.asyncio
async def test_provider_backed_natural_completion_executes_safely(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="homepage",
            arguments={"status": "complete"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky")
    task = await _make_task(
        client, headers, project_id, title="Create the homepage"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "Yeah Rocky, we're done with the homepage."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.update"

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"
    activity = await _get_activity(client, headers)
    completed = [a for a in activity if a["event_type"] == "task.completed"]
    assert len(completed) == 1
    assert completed[0]["entity_id"] == task["id"]


@pytest.mark.asyncio
async def test_provider_backed_conversation_does_not_mutate(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Yes. I can hear you.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Talk")
    task = await _make_task(client, headers, project_id, title="Homepage")

    resp = await client.post(
        "/conversation",
        json={"message": "Hello Rocky, can you hear me?"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "Yes. I can hear you."
    assert len(provider.calls) == 1
    assert provider.calls[0]["message"] == "Hello Rocky, can you hear me?"
    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_production_wiring_uses_provider_for_conversation_miss(
    ctx,
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Yes. I can hear you.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/conversation",
        json={"message": "Hello can you hear me"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "Yes. I can hear you."
    assert len(provider.calls) == 1
    assert provider.calls[0]["message"] == "Hello can you hear me"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "reply_fragment"),
    [
        ("Hello", "Hello"),
        ("Can you hear me?", "hear you"),
        ("Who are you?", "Rocky"),
        ("What can you do?", "tasks"),
        ("Tell me a joke.", "debugging"),
    ],
)
async def test_ordinary_conversation_uses_general_assistant_without_mutation(
    ctx, message: str, reply_fragment: str
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply={
                "Hello": "Hello. How can I help?",
                "Can you hear me?": "Yes, I can hear you.",
                "Who are you?": "I'm Rocky, your personal assistant.",
                "What can you do?": "I can manage your tasks and answer questions.",
                "Tell me a joke.": "Debugging: being the detective in a mystery you wrote.",
            }[message],
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert reply_fragment.lower() in body["reply"].lower()
    assert provider.calls[0]["include_personal_context"] is False
    assert provider.calls[0]["context"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "reply_fragment"),
    [
        ("What is quantum computing?", "qubit"),
        ("Explain recursion simply.", "itself"),
        ("What is photosynthesis?", "sunlight"),
        ("Explain Docker in simple terms.", "container"),
        ("What is the capital of Japan?", "Tokyo"),
    ],
)
async def test_general_knowledge_is_returned_as_a_normal_conversation(
    ctx, message: str, reply_fragment: str
) -> None:
    replies = {
        "What is quantum computing?": (
            "Quantum computing uses qubits to process information through "
            "quantum effects."
        ),
        "Explain recursion simply.": (
            "Recursion is when a solution uses a smaller version of itself "
            "until it reaches a stopping point."
        ),
        "What is photosynthesis?": (
            "Plants use sunlight to turn water and carbon dioxide into food."
        ),
        "Explain Docker in simple terms.": "Docker packages software in portable containers.",
        "What is the capital of Japan?": "The capital of Japan is Tokyo.",
    }
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply=replies[message])
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == replies[message]
    assert body["language"] == "en"
    assert uuid.UUID(body["thread_id"])
    assert reply_fragment.lower() in body["reply"].lower()
    assert provider.calls[0]["include_personal_context"] is False


@pytest.mark.asyncio
async def test_general_question_does_not_build_personal_world(
    ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="The sky looks blue because air scatters blue light more strongly.",
        )
    )

    async def _unexpected_world_build(*_args: object, **_kwargs: object) -> WorldView:
        raise AssertionError("general route must not build the personal world")

    monkeypatch.setattr(ConversationService, "_build_world", _unexpected_world_build)
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Why is the sky blue?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert "scatters blue light" in response.json()["reply"]
    assert provider.calls[0]["world"] is None


@pytest.mark.asyncio
async def test_openai_general_request_omits_rocky_world_payload() -> None:
    provider = OpenAIUnderstandingProvider.__new__(OpenAIUnderstandingProvider)
    provider._client = _FakeOpenAIClient()
    provider._model = "test-model"
    provider.last_error_code = None
    world = WorldView(
        projects=(ProjectRef(project_id=uuid.uuid4(), name="Private Project"),),
        tasks=(),
    )

    result = await provider.understand(
        message="What is the capital of Japan?",
        world=world,
        context=None,
        include_personal_context=False,
    )

    assert isinstance(result, ConversationTurn)
    call = provider._client.responses.calls[0]
    user_input = call["input"][1]["content"][0]["text"]
    payload = json.loads(user_input)
    assert payload == {
        "message": "What is the capital of Japan?",
        "context": {},
        "response_language": "en",
    }


@pytest.mark.asyncio
async def test_general_follow_up_receives_only_one_bounded_general_turn(ctx) -> None:
    first_provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Docker packages applications and dependencies into containers.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    first = await client.post(
        "/conversation",
        json={"message": "What is Docker?"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert len(first_provider.calls) == 1

    second_provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Containers share the host kernel, so they are lighter than VMs.",
        )
    )
    second = await client.post(
        "/conversation",
        json={"message": "Why would I use it instead of a VM?"},
        headers=headers,
    )

    assert second.status_code == 200, second.text
    call = second_provider.calls[0]
    assert call["include_personal_context"] is False
    assert call["context"]["previous_general_turn"] == {
        "message": "What is Docker?",
        "reply": "Docker packages applications and dependencies into containers.",
        "language": "en",
    }
    assert [turn["role"] for turn in call["context"]["recent_turns"]] == [
        "user", "assistant"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "language", "reply_fragment"),
    [
        ("मेरे काम क्या हैं?", "hi", "सक्रिय काम"),
        ("నా పనులు ఏమిటి?", "te", "యాక్టివ్ పనులు"),
        ("இன்று எனக்கு என்ன வேலைகள் உள்ளன?", "ta", "வேலைகள்"),
        ("என் பணிகள் என்ன?", "ta", "வேலைகள்"),
        ("¿Qué tareas tengo?", "es", "tareas activas"),
        ("Quelles tâches ai-je aujourd'hui ?", "fr", "tâches actives"),
        ("今日のタスクは何ですか？", "ja", "タスク"),
        ("Rocky, naa tasks today enti?", "te", "యాక్టివ్ పనులు"),
    ],
)
async def test_multilingual_task_queries_stay_deterministic_and_localized(
    ctx, message: str, language: str, reply_fragment: str
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Provider must not run.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky")
    await _make_task(client, headers, project_id, title="Ship M3")

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is True
    assert body["action"] == "task.list"
    assert body["language"] == language
    assert reply_fragment in body["reply"]
    assert "Ship M3" in body["reply"]
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "language", "reply"),
    [
        ("डॉकर क्या है?", "hi", "डॉकर कंटेनर में ऐप चलाने का तरीका है।"),
        ("Docker అంటే ఏమిటి?", "te", "Docker యాప్‌లను కంటైనర్లలో నడుపుతుంది."),
        ("Docker என்றால் என்ன?", "ta", "Docker செயலிகளை கண்டெய்னர்களில் இயக்குகிறது."),
        ("¿Qué es Docker?", "es", "Docker ejecuta aplicaciones en contenedores."),
        ("Qu'est-ce que Docker ?", "fr", "Docker exécute des applications dans des conteneurs."),
        ("Dockerとは何ですか？", "ja", "Dockerはアプリをコンテナで実行します。"),
    ],
)
async def test_multilingual_general_questions_request_same_language_without_world(
    ctx, message: str, language: str, reply: str
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply=reply)
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["language"] == language
    assert body["reply"] == reply
    call = provider.calls[0]
    assert call["response_language"] == language
    assert call["include_personal_context"] is False


@pytest.mark.asyncio
async def test_unsupported_supplied_language_does_not_override_telugu_script(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Docker యాప్‌లను కంటైనర్లలో నడుపుతుంది.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Docker అంటే ఏమిటి?", "language": "bn"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["language"] == "te"
    assert provider.calls[0]["response_language"] == "te"


@pytest.mark.asyncio
async def test_explicit_response_language_overrides_input_language(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="डॉकर एक कंटेनर प्लेटफ़ॉर्म है।")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Docker ni Hindi mein explain karo."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["language"] == "hi"
    assert provider.calls[0]["response_language"] == "hi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "language", "fragment"),
    [
        ("आज का मौसम क्या है?", "hi", "लाइव मौसम"),
        ("హైదరాబాద్‌లో weather ఎలా ఉంది?", "te", "ప్రత్యక్ష వాతావరణ"),
        ("இன்றைய வானிலை எப்படி இருக்கிறது?", "ta", "நேரடி வானிலை"),
        ("¿Qué tiempo hace hoy?", "es", "tiempo real"),
    ],
)
async def test_multilingual_live_data_refusal_is_local_and_same_language(
    ctx, message: str, language: str, fragment: str
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Fabricated weather")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["language"] == language
    assert fragment in body["reply"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_live_weather_routes_before_general_assistant(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Provider must not answer live data.")
    )
    _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            WeatherReport(
                location="Hyderabad, India",
                window="current",
                temperature_c=29.0,
                condition="partly cloudy",
                precipitation_probability=20,
                source=SourceMetadata(
                    provider="fake-weather",
                    retrieved_at=datetime.now(timezone.utc),
                    freshness="test",
                ),
            ),
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What's the weather in Hyderabad?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] == "weather.current"
    assert "Hyderabad, India" in body["reply"]
    assert "fake-weather" in body["reply"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_live_route_does_not_build_personal_world(
    ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Fabricated weather")
    )
    _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            WeatherReport(
                location="London, UK",
                window="current",
                temperature_c=14.0,
                source=SourceMetadata(
                    provider="fake-weather",
                    retrieved_at=datetime.now(timezone.utc),
                    freshness="test",
                ),
            ),
        )
    )

    async def _unexpected_world_build(*_args: object, **_kwargs: object) -> WorldView:
        raise AssertionError("live route must not build the personal world")

    monkeypatch.setattr(ConversationService, "_build_world", _unexpected_world_build)
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What's the weather in London?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "weather.current"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_live_provider_failure_is_honest_and_does_not_fallback_to_model(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Fabricated live answer")
    )
    _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            None,
            error_code="provider_timeout",
            error_message="Weather provider timed out.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What's the weather in London?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "I couldn't retrieve weather information quickly enough."
    assert provider.calls == []


@pytest.mark.asyncio
async def test_live_weather_uses_ephemeral_device_location_when_needed(ctx) -> None:
    live = _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            WeatherReport(
                location="Current location",
                window="current",
                temperature_c=26.0,
                condition="clear",
                source=SourceMetadata(
                    provider="fake-weather",
                    retrieved_at=datetime.now(timezone.utc),
                    freshness="test",
                ),
            ),
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={
            "message": "What's the weather today?",
            "location_context": {
                "latitude": 12.971,
                "longitude": 77.594,
                "accuracy_meters": 900,
                "source": "native",
            },
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "weather.current"
    intent = live.executed[0]
    assert intent.arguments["location"] == "Current location"
    assert intent.arguments["latitude"] == 12.971
    assert intent.arguments["longitude"] == 77.594


@pytest.mark.asyncio
async def test_live_weather_does_not_override_explicit_location(ctx) -> None:
    live = _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            WeatherReport(
                location="Hyderabad, India",
                window="current",
                temperature_c=29.0,
                source=SourceMetadata(
                    provider="fake-weather",
                    retrieved_at=datetime.now(timezone.utc),
                    freshness="test",
                ),
            ),
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={
            "message": "What's the weather in Hyderabad?",
            "location_context": {
                "latitude": 12.971,
                "longitude": 77.594,
                "source": "native",
            },
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    intent = live.executed[0]
    assert intent.arguments["location"] == "Hyderabad"
    assert "latitude" not in intent.arguments
    assert "longitude" not in intent.arguments


@pytest.mark.asyncio
async def test_evergreen_general_question_still_uses_general_assistant(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Photosynthesis turns light into chemical energy.",
        )
    )
    live = _use_fake_live_service(None)
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What is photosynthesis?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "Photosynthesis turns light into chemical energy."
    assert len(provider.calls) == 1
    assert live.executed == []


@pytest.mark.asyncio
async def test_deterministic_private_capability_takes_priority_over_live(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Provider must not run.")
    )
    live = _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            None,
            error_code="should_not_run",
            error_message="Live should not run.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky")
    await _make_task(client, headers, project_id, title="Ship M6")

    response = await client.post(
        "/conversation",
        json={"message": "What tasks do I have today?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "task.list"
    assert "Ship M6" in response.json()["reply"]
    assert live.executed == []
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("What are my current tasks?", "task.list"),
        ("What's my latest project?", "project.list"),
        ("What do I have today?", None),
        ("What are my reminders today?", "reminder.list"),
        ("Show my latest notes.", "note.list"),
        ("What are my current projects?", "project.list"),
    ],
)
async def test_ci5a_personal_freshness_never_executes_public_live_provider(
    ctx, message: str, expected_action: str | None
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Safe personal understanding path.")
    )
    live = _use_fake_live_service(
        LiveLookupResult(
            "web.search",
            None,
            error_code="should_not_run",
            error_message="Public live provider must not run.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == expected_action
    assert live.executed == []
    if expected_action is None:
        assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_ci5a_personal_pronoun_does_not_block_grounded_live_weather(ctx) -> None:
    live = _use_fake_live_service(
        LiveLookupResult(
            "weather.current",
            WeatherReport(
                location="Current location",
                window="current",
                temperature_c=27.0,
                source=SourceMetadata(
                    provider="fake-weather",
                    retrieved_at=datetime.now(timezone.utc),
                    freshness="test",
                ),
            ),
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={
            "message": "What's the weather near my location?",
            "location_context": {
                "latitude": 17.385,
                "longitude": 78.487,
                "source": "native",
            },
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "weather.current"
    assert len(live.executed) == 1
    intent = live.executed[0]
    assert intent.arguments["latitude"] == 17.385
    assert intent.arguments["longitude"] == 78.487


@pytest.mark.asyncio
async def test_multilingual_follow_up_carries_language_and_bounded_subject(ctx) -> None:
    first_provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Docker యాప్‌లను కంటైనర్లలో నడుపుతుంది.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "Docker అంటే ఏమిటి?"},
        headers=headers,
    )
    assert len(first_provider.calls) == 1

    second_provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="ఇది యాప్ కోసం ఒక పెట్టె లాంటిది.")
    )
    response = await client.post(
        "/conversation",
        json={"message": "ఇంకా సింపుల్‌గా చెప్పు."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    call = second_provider.calls[0]
    assert call["response_language"] == "te"
    assert call["context"]["previous_general_turn"] == {
        "message": "Docker అంటే ఏమిటి?",
        "reply": "Docker యాప్‌లను కంటైనర్లలో నడుపుతుంది.",
        "language": "te",
    }


@pytest.mark.asyncio
async def test_tamil_follow_up_carries_language_and_bounded_subject(ctx) -> None:
    first_provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="Docker செயலிகளை கண்டெய்னர்களில் இயக்குகிறது.",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "Docker என்றால் என்ன?"},
        headers=headers,
    )
    assert len(first_provider.calls) == 1

    second_provider = _use_fake_provider(
        ConversationTurn(
            kind="conversation",
            reply="இது செயலிக்கான ஒரு பெட்டி போன்றது.",
        )
    )
    response = await client.post(
        "/conversation",
        json={"message": "இன்னும் எளிமையாக சொல்லு."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    call = second_provider.calls[0]
    assert call["response_language"] == "ta"
    assert call["context"]["previous_general_turn"] == {
        "message": "Docker என்றால் என்ன?",
        "reply": "Docker செயலிகளை கண்டெய்னர்களில் இயக்குகிறது.",
        "language": "ta",
    }


@pytest.mark.asyncio
async def test_multilingual_provider_clarification_preserves_localized_prompt(ctx) -> None:
    _use_fake_provider(
        Clarification(kind="clarification", prompt="ఏ పని గురించి అడుగుతున్నారు?")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "దాని గురించి చెప్పు", "language": "te"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "ఏ పని గురించి అడుగుతున్నారు?"
    assert body["language"] == "te"
    assert uuid.UUID(body["thread_id"])


@pytest.mark.asyncio
async def test_unrelated_general_question_omits_previous_general_turn(ctx) -> None:
    first_provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Docker is a container platform.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "What is Docker?"},
        headers=headers,
    )
    assert len(first_provider.calls) == 1

    second_provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Docker usa contenedores.")
    )
    response = await client.post(
        "/conversation",
        json={"message": "¿Qué es Docker?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert second_provider.calls[0]["context"] is None


@pytest.mark.asyncio
async def test_general_question_does_not_send_grounded_personal_context(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Kubernetes orchestrates containers.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await _make_project(client, headers, name="Private Project")

    grounded = await client.post(
        "/conversation", json={"message": "Show my projects."}, headers=headers
    )
    assert grounded.json()["action"] == "project.list"
    assert provider.calls == []

    general = await client.post(
        "/conversation",
        json={"message": "What is Kubernetes?"},
        headers=headers,
    )

    assert general.status_code == 200, general.text
    call = provider.calls[0]
    assert call["include_personal_context"] is False
    assert call["context"] is None


@pytest.mark.asyncio
async def test_personal_general_hybrid_may_send_bounded_rocky_context(ctx) -> None:
    provider = _use_fake_provider(
        [
            PersonalContextRequest(kind="personal_context"),
            ConversationTurn(
                kind="conversation",
                reply="Start with the highest-impact active task.",
            ),
        ]
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Plan")
    await _make_task(client, headers, project_id, title="Ship Rocky")

    response = await client.post(
        "/conversation",
        json={"message": "Which of my tasks should I do first?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert provider.calls[0]["include_personal_context"] is False
    assert provider.calls[0]["world"] is None
    call = provider.calls[1]
    assert call["include_personal_context"] is True
    world = call["world"]
    assert isinstance(world, WorldView)
    assert [task.title for task in world.tasks] == ["Ship Rocky"]


@pytest.mark.asyncio
async def test_live_information_request_is_honest_without_provider_call(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="It is sunny.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What is the weather today?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert "don't have live access to weather" in body["reply"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_current_news_never_uses_general_model_knowledge(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="Fabricated current news")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "What is the latest AI news?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["executed"] is False
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("What tasks do I have?", "task.list"),
        ("Show my projects.", "project.list"),
        ("What did I do yesterday?", "activity.recall"),
    ],
)
async def test_known_capabilities_never_pay_general_assistant_latency(
    ctx, message: str, expected_action: str
) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="This must not be used.")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation", json={"message": message}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == expected_action
    assert provider.calls == []


@pytest.mark.asyncio
async def test_provider_unknown_action_is_rejected(ctx) -> None:
    _use_fake_provider(
        ActionProposal(kind="action", action="system.delete_everything")
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Safe")
    task = await _make_task(client, headers, project_id, title="Homepage")

    resp = await client.post(
        "/conversation",
        json={"message": "delete everything"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["reply"] == "I can't do that yet."
    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_provider_registered_action_rejects_extra_arguments(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="Homepage",
            arguments={"status": "complete", "user_id": str(uuid.uuid4())},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Strict")
    task = await _make_task(client, headers, project_id, title="Homepage")

    response = await client.post(
        "/conversation",
        json={"message": "The homepage can be wrapped up"},
        headers=headers,
    )
    assert response.json()["executed"] is False
    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_provider_failure_degrades_without_mutation(ctx) -> None:
    _use_fake_provider(UnderstandingProviderError("boom"))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Fallback")
    task = await _make_task(client, headers, project_id, title="Homepage")

    resp = await client.post(
        "/conversation",
        json={"message": "That homepage thing is finished."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_general_provider_failure_returns_clean_non_executing_fallback(ctx) -> None:
    _use_fake_provider(UnderstandingProviderError("boom"))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Why do leaves change color?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "I couldn't answer that right now. Please try again."
    assert body["language"] == "en"
    assert uuid.UUID(body["thread_id"])


@pytest.mark.asyncio
async def test_malformed_provider_result_fails_closed(ctx) -> None:
    _use_fake_provider(object())
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Malformed")
    task = await _make_task(client, headers, project_id, title="Homepage")

    resp = await client.post(
        "/conversation",
        json={"message": "That homepage thing is finished."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "I'm not sure what you want me to do."

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_provider_recall_uses_activity_responder(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="activity.recall",
            recall_window="yesterday",
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Recall")
    task = await _make_task(client, headers, project_id, title="yesterday")
    activity = await _get_activity(client, headers)
    created = next(a for a in activity if a["entity_id"] == task["id"])
    await _set_activity_created_at(
        sessionmaker,
        created["id"],
        _local_datetime_for_day_offset("UTC", -1, time(hour=10)),
    )

    resp = await client.post(
        "/conversation",
        json={"message": "What were we working on yesterday?"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "activity.recall"
    assert body["reply"].startswith("Yesterday:")
    assert "task.created" not in body["reply"]


@pytest.mark.asyncio
async def test_provider_missing_entity_does_not_mutate(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="homepage",
            arguments={"status": "complete"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/conversation",
        json={"message": "I finished the homepage."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert "couldn't find an active task" in body["reply"]


@pytest.mark.asyncio
async def test_provider_ambiguous_entity_does_not_mutate(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="homepage",
            arguments={"status": "complete"},
        )
    )
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Ambiguous")
    await _make_task(client, headers, project_id, title="homepage hero")
    await _make_task(client, headers, project_id, title="homepage footer")

    resp = await client.post(
        "/conversation",
        json={"message": "That homepage thing is complete."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert "several" in body["reply"].lower()


@pytest.mark.asyncio
async def test_provider_cannot_bypass_ownership(ctx) -> None:
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="homepage",
            arguments={"status": "complete"},
        )
    )
    client, sessionmaker = ctx
    owner_headers, _ = await _auth_headers(client, sessionmaker)
    other_headers, _ = await _auth_headers(client, sessionmaker)
    owner_project = await _make_project(
        client, owner_headers, name="Owner"
    )
    owner_task = await _make_task(
        client, owner_headers, owner_project, title="Homepage"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "I finished the homepage."},
        headers=other_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["executed"] is False

    fetched = await _get_task(
        client, owner_headers, owner_project, owner_task["id"]
    )
    assert fetched["status"] == "active"


@pytest.mark.asyncio
async def test_contextual_reference_can_complete_last_grounded_task(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Context")
    task = await _make_task(
        client, headers, project_id, title="Create the homepage"
    )

    _use_fake_provider(
        ConversationTurn(kind="conversation", reference="homepage")
    )
    first = await client.post(
        "/conversation",
        json={"message": "What about the homepage?"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert "still active" in first.json()["reply"]

    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="task.update",
            reference="that",
            arguments={"status": "complete"},
        )
    )
    second = await client.post(
        "/conversation",
        json={"message": "That's done too."},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["executed"] is True

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"


# --------------------------------------------------------------------------
# Read grounding.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_list_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await _make_project(client, headers, name="Alpha")
    await _make_project(client, headers, name="Beta")

    resp = await client.post(
        "/conversation",
        json={"message": "what am I working on"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "project.list"
    assert "Alpha" in body["reply"] and "Beta" in body["reply"]


@pytest.mark.asyncio
async def test_task_list_via_conversation(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Gamma")
    await _make_task(client, headers, project_id, title="one")
    await _make_task(client, headers, project_id, title="two")

    resp = await client.post(
        "/conversation",
        json={"message": "tasks in gamma"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.list"


@pytest.mark.asyncio
async def test_natural_task_status_question_uses_task_list(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/conversation",
        json={"message": "What tasks do I have today?"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.list"


@pytest.mark.asyncio
async def test_lined_up_question_reports_no_active_tasks_before_history(
    ctx,
) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Today")
    await _make_task(
        client, headers, project_id, title="Create the homepage"
    )

    complete = await client.post(
        "/conversation",
        json={"message": "finish the homepage task"},
        headers=headers,
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["executed"] is True

    resp = await client.post(
        "/conversation",
        json={"message": "Do we have anything specific lined up for today?"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.list"
    assert "don't have any active tasks" in body["reply"]
    assert "Create the homepage" in body["reply"]
    assert "Recently:" not in body["reply"]


@pytest.mark.asyncio
async def test_lined_up_question_reports_active_task_names(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Today")
    await _make_task(client, headers, project_id, title="Prepare roadmap")
    await _make_task(client, headers, project_id, title="Review launch notes")

    resp = await client.post(
        "/conversation",
        json={"message": "What should I work on?"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "task.list"
    assert "Prepare roadmap" in body["reply"]
    assert "Review launch notes" in body["reply"]


# --------------------------------------------------------------------------
# Safety edge 1: no match -> no mutation.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_match_does_not_mutate(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Delta")
    task = await _make_task(client, headers, project_id, title="real task")

    resp = await client.post(
        "/conversation",
        json={"message": "complete the nonexistent thing"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert (
        body["reply"]
        == "I understood that you want to finish a task, but I couldn't "
        "find an active task called 'nonexistent thing'."
    )

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"
    activity = await _get_activity(client, headers)
    assert not [a for a in activity if a["event_type"] == "task.completed"]


@pytest.mark.asyncio
async def test_unknown_intent_is_distinct_from_missing_task(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/conversation",
        json={"message": "flarb the moon cabbage"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["action"] is None
    assert body["reply"] == "I'm not sure what you want me to do."


@pytest.mark.asyncio
async def test_completion_ignores_already_completed_task(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Done")
    task = await _make_task(
        client, headers, project_id, title="Homepage", status="complete"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "finish homepage"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert body["reply"] == (
        "I understood that you want to finish a task, but I couldn't find "
        "an active task called 'homepage'."
    )

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "complete"
    activity = await _get_activity(client, headers)
    assert not [a for a in activity if a["event_type"] == "task.completed"]


# --------------------------------------------------------------------------
# Safety edge 2: ambiguous match -> no mutation.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_match_does_not_mutate(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Epsilon")
    t1 = await _make_task(
        client, headers, project_id, title="homepage hero"
    )
    t2 = await _make_task(
        client, headers, project_id, title="homepage footer"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "complete homepage"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False
    assert "several" in body["reply"].lower()

    for tid in (t1["id"], t2["id"]):
        fetched = await _get_task(client, headers, project_id, tid)
        assert fetched["status"] == "active"
    activity = await _get_activity(client, headers)
    assert not [a for a in activity if a["event_type"] == "task.completed"]


# --------------------------------------------------------------------------
# Disambiguation: a longer phrase narrows an otherwise-ambiguous set to one.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phrase_disambiguates_to_single_task(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Eta")
    hero = await _make_task(
        client, headers, project_id, title="homepage hero"
    )
    footer = await _make_task(
        client, headers, project_id, title="homepage footer"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "finish homepage hero"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True

    fetched_hero = await _get_task(client, headers, project_id, hero["id"])
    assert fetched_hero["status"] == "complete"
    fetched_footer = await _get_task(
        client, headers, project_id, footer["id"]
    )
    assert fetched_footer["status"] == "active"

    activity = await _get_activity(client, headers)
    completed = [a for a in activity if a["event_type"] == "task.completed"]
    assert len(completed) == 1


# --------------------------------------------------------------------------
# Safety edge 3: verb-gating -> title match without a verb never executes.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_title_match_without_verb_does_not_execute(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Zeta")
    task = await _make_task(
        client, headers, project_id, title="homepage copy"
    )

    resp = await client.post(
        "/conversation",
        json={"message": "the homepage copy task"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is False

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"


# --------------------------------------------------------------------------
# activity.recall: grounded narration of the real ledger.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_narrates_real_activity(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Recall")
    task = await _make_task(client, headers, project_id, title="ship it")

    # Complete it through the domain path so the ledger gets a real
    # task.completed event alongside task.created.
    upd = await client.patch(
        f"/projects/{project_id}/tasks/{task['id']}",
        json={"status": "complete"},
        headers=headers,
    )
    assert upd.status_code == 200, upd.text

    resp = await client.post(
        "/conversation",
        json={"message": "what did I do"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "activity.recall"
    reply = body["reply"]
    assert reply.startswith("Recently:")
    # Grounded: it names events that really happened, and nothing it invents.
    assert "completed" in reply
    assert "created" in reply
    assert "project.created" not in reply
    assert "task.completed" not in reply


@pytest.mark.asyncio
async def test_recall_uses_resolved_task_name_instead_of_id(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Rocky")
    task = await _make_task(
        client, headers, project_id, title="Need to work on rocky"
    )

    upd = await client.patch(
        f"/projects/{project_id}/tasks/{task['id']}",
        json={"status": "complete"},
        headers=headers,
    )
    assert upd.status_code == 200, upd.text

    resp = await client.post(
        "/conversation",
        json={"message": "where did we leave off"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    reply = resp.json()["reply"]
    assert "completed 'Need to work on rocky'" in reply
    assert task["id"] not in reply
    assert task["id"][:8] not in reply
    assert "task " + task["id"][:8] not in reply


@pytest.mark.asyncio
async def test_recall_humanizes_project_created(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await _make_project(client, headers, name="TestDev")

    resp = await client.post(
        "/conversation",
        json={"message": "where did we leave off"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    reply = resp.json()["reply"]
    assert "created the 'TestDev' project" in reply
    assert "project.created" not in reply


@pytest.mark.asyncio
async def test_recall_empty_ledger_is_honest(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/conversation",
        json={"message": "where did we leave off"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "activity.recall"
    assert body["reply"] == "I don't have any recent activity recorded."


@pytest.mark.asyncio
async def test_recall_execute_recall_continuity(ctx) -> None:
    # The demo arc: ask history, act, ask history again and see the act.
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Arc")
    await _make_task(client, headers, project_id, title="homepage")

    first = await client.post(
        "/conversation",
        json={"message": "catch me up"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_reply = first.json()["reply"]
    assert "completed" not in first_reply

    act = await client.post(
        "/conversation",
        json={"message": "finish homepage"},
        headers=headers,
    )
    assert act.status_code == 200, act.text
    assert act.json()["executed"] is True

    second = await client.post(
        "/conversation",
        json={"message": "catch me up"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_reply = second.json()["reply"]
    assert second_reply.startswith("Recently:")
    assert "completed" in second_reply
    assert "task.completed" not in second_reply


@pytest.mark.asyncio
async def test_yesterday_recall_uses_user_local_calendar_day(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Calendar")
    yesterday_task = await _make_task(
        client, headers, project_id, title="yesterday item"
    )
    today_task = await _make_task(
        client, headers, project_id, title="today item"
    )

    activity = await _get_activity(client, headers)
    by_entity = {a["entity_id"]: a for a in activity}
    await _set_activity_created_at(
        sessionmaker,
        by_entity[yesterday_task["id"]]["id"],
        _local_datetime_for_day_offset(
            "America/Los_Angeles", -1, time(hour=10)
        ),
    )
    await _set_activity_created_at(
        sessionmaker,
        by_entity[today_task["id"]]["id"],
        _local_datetime_for_day_offset(
            "America/Los_Angeles", 0, time(hour=10)
        ),
    )

    resp = await client.post(
        "/conversation",
        json={
            "message": "What did I do yesterday?",
            "timezone": "America/Los_Angeles",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["action"] == "activity.recall"
    assert body["reply"].startswith("Yesterday:")
    assert not body["reply"].startswith("Recently:")
    assert "yesterday item" in body["reply"]
    assert "today item" not in body["reply"]


@pytest.mark.asyncio
async def test_yesterday_recall_is_honest_when_empty(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Calendar")
    task = await _make_task(client, headers, project_id, title="today only")
    activity = await _get_activity(client, headers)
    created = next(a for a in activity if a["entity_id"] == task["id"])
    await _set_activity_created_at(
        sessionmaker,
        created["id"],
        _local_datetime_for_day_offset("Asia/Kolkata", 0, time(hour=10)),
    )

    resp = await client.post(
        "/conversation",
        json={
            "message": "What did I do yesterday?",
            "timezone": "Asia/Kolkata",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert (
        resp.json()["reply"]
        == "I don't have any activity from yesterday recorded."
    )


@pytest.mark.asyncio
async def test_yesterday_recall_invalid_timezone_falls_back_to_utc(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Calendar")
    task = await _make_task(client, headers, project_id, title="utc item")
    activity = await _get_activity(client, headers)
    created = next(a for a in activity if a["entity_id"] == task["id"])
    await _set_activity_created_at(
        sessionmaker,
        created["id"],
        _local_datetime_for_day_offset("UTC", -1, time(hour=10)),
    )

    resp = await client.post(
        "/conversation",
        json={
            "message": "What did I do yesterday?",
            "timezone": "Not/AZone",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "utc item" in resp.json()["reply"]


@pytest.mark.asyncio
async def test_unresolved_recall_entity_never_leaks_identifier(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    orphan_id = uuid.uuid4()
    await _record_activity(
        sessionmaker,
        user_id=user_id,
        event_type="task.completed",
        entity_type="task",
        entity_id=orphan_id,
        created_at=datetime.now(timezone.utc),
    )

    resp = await client.post(
        "/conversation",
        json={"message": "where did we leave off"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    reply = resp.json()["reply"]
    assert "completed a task" in reply
    assert str(orphan_id) not in reply
    assert str(orphan_id)[:8] not in reply


# --------------------------------------------------------------------------
# Semantic boundary contract: "what happened" -> recall, "what exists now"
# -> project.list. Pinned so a future LlmResolver must preserve the routing.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_past_working_on_routes_to_recall(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Bound")
    await _make_task(client, headers, project_id, title="thing")

    resp = await client.post(
        "/conversation",
        json={"message": "what was I working on"},
        headers=headers,
    )
    body = resp.json()
    assert body["action"] == "activity.recall"


@pytest.mark.asyncio
async def test_boundary_present_working_on_routes_to_project_list(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await _make_project(client, headers, name="Bound")

    resp = await client.post(
        "/conversation",
        json={"message": "what am I working on"},
        headers=headers,
    )
    body = resp.json()
    assert body["action"] == "project.list"
    assert body["reply"].startswith("You have")


@pytest.mark.asyncio
async def test_boundary_what_changed_routes_to_recall(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    project_id = await _make_project(client, headers, name="Bound")
    await _make_task(client, headers, project_id, title="thing")

    resp = await client.post(
        "/conversation",
        json={"message": "what changed recently"},
        headers=headers,
    )
    body = resp.json()
    assert body["action"] == "activity.recall"


@pytest.mark.asyncio
async def test_boundary_my_projects_routes_to_project_list(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await _make_project(client, headers, name="Bound")

    resp = await client.post(
        "/conversation",
        json={"message": "my projects"},
        headers=headers,
    )
    body = resp.json()
    assert body["action"] == "project.list"


# --------------------------------------------------------------------------
# Safety edge 4: unknown action -> refused at the trust boundary.
#
# Cannot arise through HTTP (the HTTP path uses HardcodedResolver, which only
# ever proposes registry names). Exercised by injecting a stub resolver that
# proposes an off-registry action into ConversationService directly.
# --------------------------------------------------------------------------


class _RogueResolver:
    """Proposes an action name that is not in the closed registry."""

    def resolve(self, message: str, world: WorldView) -> ResolvedAction:
        return ResolvedAction(action="task.delete")


class _InvalidProjectResolver:
    def resolve(self, message: str, world: WorldView) -> ResolvedAction:
        return ResolvedAction(action=registry.PROJECT_CREATE)


class _WhitespaceProjectResolver:
    def resolve(self, message: str, world: WorldView) -> ResolvedAction:
        return ResolvedAction(
            action=registry.PROJECT_CREATE,
            project_name="  Japan Trip  ",
        )


@pytest.mark.asyncio
async def test_unknown_action_refused(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, resolver=_RogueResolver())
        with pytest.raises(UnknownActionError):
            await service.handle(user, "delete everything")


@pytest.mark.asyncio
async def test_invalid_deterministic_action_never_reaches_domain_mutation(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, resolver=_InvalidProjectResolver())
        service._projects.create_project = AsyncMock()

        with pytest.raises(ActionArgumentsError):
            await service.handle(user, "Create an unnamed project")

        service._projects.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_deterministic_execution_consumes_validated_values(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, resolver=_WhitespaceProjectResolver())
        response = await service.handle(user, "Create the Japan project")
        projects = await service._projects.list_projects(user)

    assert response.executed is True
    assert [project.name for project in projects] == ["Japan Trip"]


@pytest.mark.asyncio
async def test_registered_execution_returns_normalized_result_envelope(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session)
        result = await service._execute_action(
            user,
            ResolvedAction(
                action=registry.PROJECT_CREATE,
                project_name="Japan Trip",
            ),
            WorldView(projects=(), tasks=()),
        )

    assert result.success is True
    assert result.action == registry.PROJECT_CREATE
    assert result.payload == {"kind": "project_created", "executed": True}
    assert result.display_summary == "project created: Japan Trip"
    assert len(result.entity_ids) == 1
    assert result.references[0].kind == "project"
    assert result.references[0].display_text == "Japan Trip"
    assert result.references[0].entity_id == result.entity_ids[0]


def _proposed_plan_step(
    number: int,
    action: str,
    arguments: dict[str, str] | None = None,
    *,
    result_of: str | None = None,
    reference: str | None = None,
) -> ProposedPlanStep:
    return ProposedPlanStep(
        id=f"step_{number}", action=action, arguments=arguments or {},
        reference=reference, result_of=result_of,
        depends_on=[result_of] if result_of else [],
        purpose=f"Run {action}",
    )


@pytest.mark.asyncio
async def test_safe_project_and_tasks_plan_executes_in_order(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "project.create", {"name": "Japan Trip"}),
        _proposed_plan_step(2, "task.create", {"title": "Book Flights"}, result_of="step_1"),
        _proposed_plan_step(3, "task.create", {"title": "Reserve Hotel"}, result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    response = await client.post(
        "/conversation",
        json={"message": "Create a Japan Trip project and add Book Flights, then add Reserve Hotel"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["executed"] is True
    assert response.json()["action"] == "plan"
    projects = (await client.get("/projects", headers=headers)).json()
    assert [project["name"] for project in projects] == ["Japan Trip"]
    tasks = (await client.get(f"/projects/{projects[0]['id']}/tasks", headers=headers)).json()
    assert [task["title"] for task in tasks] == ["Book Flights", "Reserve Hotel"]


@pytest.mark.asyncio
async def test_result_grounded_create_plan_does_not_load_broad_world(ctx) -> None:
    provider = _FakeUnderstandingProvider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "project.create", {"name": "Lean"}),
        _proposed_plan_step(2, "task.create", {"title": "Bounded"}, result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, understanding_provider=provider)
        service._build_world = AsyncMock()
        response = await service.handle(
            user, "Create project Lean and then add task Bounded"
        )
        service._build_world.assert_not_awaited()

    assert response.executed is True


@pytest.mark.asyncio
async def test_risky_plan_is_durable_confirmed_once_and_cannot_replay(ctx) -> None:
    provider = _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Old Ideas", "content": "draft"}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    proposed = await client.post(
        "/conversation",
        json={"message": "Create a note called Old Ideas and then archive it"},
        headers=headers,
    )
    assert proposed.json()["executed"] is False
    assert "Shall I proceed" in proposed.json()["reply"]
    assert (await client.get("/notes?status=archived", headers=headers)).json() == []

    confirmed = await client.post(
        "/conversation", json={"message": "Yes"}, headers=headers
    )
    assert confirmed.json()["executed"] is True
    archived = (await client.get("/notes?status=archived", headers=headers)).json()
    assert [note["title"] for note in archived] == ["Old Ideas"]

    duplicate = await client.post(
        "/conversation", json={"message": "Yes"}, headers=headers
    )
    assert duplicate.json()["executed"] is False
    assert "isn't a pending plan" in duplicate.json()["reply"]
    assert len((await client.get("/notes?status=archived", headers=headers)).json()) == 1
    assert len(provider.calls) == 1

    async with sessionmaker() as session:
        stored = await session.scalar(select(PendingConversationPlan))
        assert stored is not None
        assert stored.status == "completed"
        assert stored.result_payload["status"] == "success"


@pytest.mark.asyncio
async def test_pending_plan_rejection_executes_nothing(ctx) -> None:
    provider = _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "list.create", {"title": "Old"}),
        _proposed_plan_step(2, "list.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    await client.post(
        "/conversation",
        json={"message": "Create an Old list and then archive it"},
        headers=headers,
    )
    rejected = await client.post(
        "/conversation", json={"message": "Cancel"}, headers=headers
    )

    assert rejected.json()["executed"] is False
    assert (await client.get("/lists", headers=headers)).json() == []
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_pending_plan_is_scoped_to_its_thread(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Private", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    first = (await client.post("/conversation/threads", json={"title": "First"}, headers=headers)).json()["id"]
    second = (await client.post("/conversation/threads", json={"title": "Second"}, headers=headers)).json()["id"]

    await client.post(
        "/conversation",
        json={"message": "Create a Private note and then archive it", "thread_id": first},
        headers=headers,
    )
    wrong_thread = await client.post(
        "/conversation", json={"message": "Proceed", "thread_id": second}, headers=headers
    )
    assert wrong_thread.json()["executed"] is False
    assert (await client.get("/notes?status=archived", headers=headers)).json() == []

    right_thread = await client.post(
        "/conversation", json={"message": "Proceed", "thread_id": first}, headers=headers
    )
    assert right_thread.json()["executed"] is True


@pytest.mark.asyncio
async def test_expired_pending_plan_executes_nothing(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Expired", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    proposed = await client.post(
        "/conversation",
        json={"message": "Create an Expired note and then archive it"},
        headers=headers,
    )
    assert proposed.json()["executed"] is False
    async with sessionmaker() as session:
        stored = await session.scalar(select(PendingConversationPlan))
        assert stored is not None
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    expired = await client.post(
        "/conversation", json={"message": "Confirm"}, headers=headers
    )
    assert expired.json()["executed"] is False
    assert "expired" in expired.json()["reply"]
    assert (await client.get("/notes?status=archived", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_confirmation_revalidates_changed_reference_before_any_step(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "project.create", {"name": "Must Not Exist"}),
        _proposed_plan_step(2, "note.archive", reference="Old Ideas"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    note = (await client.post(
        "/notes", json={"title": "Old Ideas", "content": "draft"}, headers=headers
    )).json()
    await client.post(
        "/conversation",
        json={"message": "Create project Must Not Exist and then archive Old Ideas"},
        headers=headers,
    )
    await client.patch(
        f"/notes/{note['id']}", json={"status": "archived"}, headers=headers
    )

    confirmed = await client.post(
        "/conversation", json={"message": "Proceed"}, headers=headers
    )
    assert confirmed.json()["executed"] is False
    assert "revalidate" in confirmed.json()["reply"]
    assert (await client.get("/projects", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_new_pending_plan_supersedes_old_without_resurfacing(ctx) -> None:
    first_plan = PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Old Plan", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ]))
    second_plan = PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "New Plan", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ]))
    provider = _use_fake_provider([first_plan, second_plan])
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    await client.post(
        "/conversation",
        json={"message": "Create Old Plan note and then archive it"},
        headers=headers,
    )
    await client.post(
        "/conversation",
        json={"message": "Instead create New Plan note and then archive it"},
        headers=headers,
    )
    async with sessionmaker() as session:
        rows = list((await session.scalars(select(PendingConversationPlan))).all())
        assert [row.status for row in rows].count("pending") == 1
        assert [row.status for row in rows].count("rejected") == 1

    confirmed = await client.post(
        "/conversation", json={"message": "Confirm"}, headers=headers
    )
    assert confirmed.json()["executed"] is True
    duplicate = await client.post(
        "/conversation", json={"message": "Confirm"}, headers=headers
    )
    assert duplicate.json()["executed"] is False
    archived = (await client.get("/notes?status=archived", headers=headers)).json()
    assert [note["title"] for note in archived] == ["New Plan"]
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_database_rejects_two_pending_rows_for_same_owner_thread(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Unique", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "Create Unique note and then archive it"},
        headers=headers,
    )

    async with sessionmaker() as session:
        existing = await session.scalar(select(PendingConversationPlan))
        assert existing is not None
        session.add(PendingConversationPlan(
            user_id=existing.user_id,
            thread_id=existing.thread_id,
            status="pending",
            version=1,
            plan_payload=existing.plan_payload,
            expires_at=existing.expires_at,
        ))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_competing_plan_storage_conflict_returns_safe_response(ctx) -> None:
    provider = _FakeUnderstandingProvider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Conflict", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, understanding_provider=provider)
        service._pending_plans.create = AsyncMock(
            side_effect=PendingPlanConflictError("competing plan")
        )
        response = await service.handle(
            user, "Create Conflict note and then archive it"
        )

    assert response.executed is False
    assert "Another request updated" in response.reply


@pytest.mark.asyncio
async def test_atomic_claim_has_one_winner_and_expiry_cannot_win(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Claim", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "Create Claim note and then archive it"},
        headers=headers,
    )

    async with sessionmaker() as first_session, sessionmaker() as second_session:
        first_row = await first_session.scalar(select(PendingConversationPlan))
        second_row = await second_session.scalar(select(PendingConversationPlan))
        assert first_row is not None and second_row is not None
        now = datetime.now(timezone.utc)
        first_won, second_won = await asyncio.gather(
            PendingPlanStore(first_session).claim(first_row, now),
            PendingPlanStore(second_session).claim(second_row, now),
        )
    assert sorted((first_won, second_won)) == [False, True]

    async with sessionmaker() as session:
        executing = await session.scalar(select(PendingConversationPlan))
        assert executing is not None
        assert executing.status == "executing"
        assert await PendingPlanStore(session).current(
            executing.user_id, executing.thread_id, datetime.now(timezone.utc)
        ) is None

        executing.status = "pending"
        executing.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        assert await PendingPlanStore(session).claim(
            executing, datetime.now(timezone.utc)
        ) is False


@pytest.mark.asyncio
async def test_finish_detects_failed_terminal_transition(ctx) -> None:
    _use_fake_provider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Finish", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    await client.post(
        "/conversation",
        json={"message": "Create Finish note and then archive it"},
        headers=headers,
    )
    async with sessionmaker() as session:
        row = await session.scalar(select(PendingConversationPlan))
        assert row is not None
        store = PendingPlanStore(session)
        assert await store.claim(row, datetime.now(timezone.utc)) is True
        await store.finish(
            row, status="failed", result_payload={"status": "failed"},
            now=datetime.now(timezone.utc),
        )
        with pytest.raises(PlanLifecycleTransitionError):
            await store.finish(
                row, status="completed", result_payload={"status": "success"},
                now=datetime.now(timezone.utc),
            )


@pytest.mark.asyncio
async def test_result_memory_failure_is_partial_and_cannot_replay(ctx) -> None:
    provider = _FakeUnderstandingProvider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Remember Once", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        proposed = await ConversationService(
            session, understanding_provider=provider
        ).handle(user, "Create Remember Once note and then archive it")
        assert proposed.executed is False

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, understanding_provider=provider)
        service._remember_outcome = AsyncMock(
            side_effect=RuntimeError("grounding storage unavailable")
        )
        confirmed = await service.handle(user, "Confirm")

    assert confirmed.executed is True
    assert "action succeeded" in confirmed.reply
    async with sessionmaker() as session:
        row = await session.scalar(select(PendingConversationPlan))
        assert row is not None
        assert row.status == "failed"
        assert row.result_payload["status"] == "partial"

    active = (await client.get("/notes?status=active", headers=headers)).json()
    archived = (await client.get("/notes?status=archived", headers=headers)).json()
    assert [note["title"] for note in active] == ["Remember Once"]
    assert archived == []

    duplicate = await client.post(
        "/conversation", json={"message": "Confirm"}, headers=headers
    )
    assert duplicate.json()["executed"] is False
    assert len((await client.get("/notes?status=active", headers=headers)).json()) == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_terminal_persistence_failure_stays_executing_and_nonreplayable(ctx) -> None:
    provider = _FakeUnderstandingProvider(PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Terminal Once", "content": ""}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ])))
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        await ConversationService(
            session, understanding_provider=provider
        ).handle(user, "Create Terminal Once note and then archive it")

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, understanding_provider=provider)
        service._pending_plans.finish = AsyncMock(
            side_effect=PlanLifecycleTransitionError("lost terminal transition")
        )
        confirmed = await service.handle(user, "Confirm")

    assert confirmed.executed is True
    assert "will not be retried" in confirmed.reply
    async with sessionmaker() as session:
        row = await session.scalar(select(PendingConversationPlan))
        assert row is not None
        assert row.status == "executing"

    duplicate = await client.post(
        "/conversation", json={"message": "Confirm"}, headers=headers
    )
    assert duplicate.json()["executed"] is False
    archived = (await client.get("/notes?status=archived", headers=headers)).json()
    assert [note["title"] for note in archived] == ["Terminal Once"]
    assert len(provider.calls) == 1


# --------------------------------------------------------------------------
# Optional TypeSafe/Jev structured decisions.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_action_and_live_routes_skip_decision_provider(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    decisions = _FakeDecisionProvider()

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(session, decision_provider=decisions)
        created = await service.handle(user, "Create project Deterministic")
        live = await service.handle(user, "What's the weather today?")

    assert created.executed is True
    assert live.executed is False
    assert decisions.route_calls == []


@pytest.mark.asyncio
async def test_high_confidence_ambiguous_route_can_short_circuit_only_routing(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(
        ConversationTurn(kind="conversation", reply="should not be used")
    )
    decisions = _FakeDecisionProvider(
        route=RouteDecision(
            RouteKind.UNSUPPORTED, 0.96, {"unsupported": 0.96}
        )
    )

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        response = await ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
        ).handle(user, "Handle my quantum teleportation account")

    assert response.executed is False
    assert "can't safely handle" in response.reply
    assert decisions.route_calls == ["Handle my quantum teleportation account"]
    assert understanding.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [
        RouteDecision(RouteKind.UNSUPPORTED, 0.40, {"unsupported": 0.40}),
        DecisionProviderError("decision provider unavailable"),
    ],
)
async def test_low_confidence_or_unavailable_route_falls_back(ctx, decision) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(
        ConversationTurn(kind="conversation", reply="Existing provider fallback")
    )
    decisions = _FakeDecisionProvider(route=decision)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        response = await ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
        ).handle(user, "Tell me about my unusual request")

    assert response.reply == "Existing provider fallback"
    assert len(understanding.calls) == 1


def _risky_note_plan() -> PlanProposal:
    return PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "note.create", {"title": "Decision", "content": "private"}),
        _proposed_plan_step(2, "note.archive", result_of="step_1"),
    ]))


@pytest.mark.asyncio
@pytest.mark.parametrize(("phrase", "executed"), [("Yes", True), ("No", False)])
async def test_deterministic_confirmation_skips_decision_provider(
    ctx, phrase: str, executed: bool
) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_risky_note_plan())
    decisions = _FakeDecisionProvider()
    policy = DecisionPolicy(route_enabled=False, plan_verification_enabled=False)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=policy,
        )
        await service.handle(user, "Create Decision note and then archive it")
        response = await service.handle(user, phrase)

    assert response.executed is executed
    assert decisions.confirmation_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "message", "executed", "stored_status"),
    [
        (ConfirmationKind.CONFIRM, "yeah that's fine", True, "completed"),
        (ConfirmationKind.REJECT, "that's not what I want", False, "rejected"),
    ],
)
async def test_high_confidence_ambiguous_confirmation_uses_existing_lifecycle(
    ctx,
    kind: ConfirmationKind,
    message: str,
    executed: bool,
    stored_status: str,
) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_risky_note_plan())
    decisions = _FakeDecisionProvider(
        confirmation=ConfirmationDecision(
            kind, 0.96, {kind.value: 0.96}
        )
    )
    policy = DecisionPolicy(route_enabled=False, plan_verification_enabled=False)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=policy,
        )
        await service.handle(user, "Create Decision note and then archive it")
        response = await service.handle(user, message)
        stored = await session.scalar(select(PendingConversationPlan))

    assert response.executed is executed
    assert len(decisions.confirmation_calls) == 1
    assert "note.create" in decisions.confirmation_calls[0][1]
    assert "private" not in decisions.confirmation_calls[0][1]
    assert stored is not None and stored.status == stored_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [
        ConfirmationDecision(
            ConfirmationKind.CONFIRM, 0.50, {"confirm": 0.50}
        ),
        DecisionProviderError("decision provider unavailable"),
    ],
)
async def test_uncertain_confirmation_never_executes_and_remains_pending(
    ctx, decision
) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_risky_note_plan())
    decisions = _FakeDecisionProvider(confirmation=decision)
    policy = DecisionPolicy(route_enabled=False, plan_verification_enabled=False)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=policy,
        )
        await service.handle(user, "Create Decision note and then archive it")
        response = await service.handle(user, "sounds okay I suppose")
        pending = await session.scalar(select(PendingConversationPlan))

    assert response.executed is False
    assert "not confident" in response.reply
    assert pending is not None and pending.status == "pending"


@pytest.mark.asyncio
async def test_amendment_invalidates_old_plan_then_routes_fresh(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider([
        _risky_note_plan(),
        ConversationTurn(kind="conversation", reply="Amendment received."),
    ])
    decisions = _FakeDecisionProvider(
        confirmation=ConfirmationDecision(
            ConfirmationKind.AMEND, 0.97, {"amend": 0.97}
        )
    )
    policy = DecisionPolicy(route_enabled=False, plan_verification_enabled=False)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=policy,
        )
        await service.handle(user, "Create Decision note and then archive it")
        response = await service.handle(user, "Actually leave out the archive")
        pending = await session.scalar(select(PendingConversationPlan))

    assert response.reply == "Amendment received."
    assert pending is not None and pending.status == "rejected"


@pytest.mark.asyncio
async def test_unrelated_pending_response_routes_normally_without_approving_plan(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider([
        _risky_note_plan(),
        ConversationTurn(kind="conversation", reply="Separate answer."),
    ])
    decisions = _FakeDecisionProvider(
        confirmation=ConfirmationDecision(
            ConfirmationKind.UNRELATED, 0.96, {"unrelated": 0.96}
        )
    )
    policy = DecisionPolicy(route_enabled=False, plan_verification_enabled=False)

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=policy,
        )
        await service.handle(user, "Create Decision note and then archive it")
        response = await service.handle(user, "What is a haiku?")
        pending = await session.scalar(select(PendingConversationPlan))

    assert response.reply == "Separate answer."
    assert pending is not None and pending.status == "pending"


def _safe_project_plan() -> PlanProposal:
    return PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "project.create", {"name": "Verified One"}),
        _proposed_plan_step(2, "project.create", {"name": "Verified Two"}),
    ]))


@pytest.mark.asyncio
async def test_faithful_plan_still_uses_deterministic_compiler_and_executes(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_safe_project_plan())
    decisions = _FakeDecisionProvider()

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        response = await ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=DecisionPolicy(route_enabled=False),
        ).handle(user, "Create project Verified One and then create project Verified Two")

    assert response.executed is True
    assert len(decisions.verification_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verification",
    [
        PlanVerificationDecision(0.90, 0.02, 0.01, 0.10),
        PlanVerificationDecision(0.02, 0.90, 0.01, 0.10),
        PlanVerificationDecision(0.02, 0.02, 0.90, 0.10),
    ],
    ids=["unrequested-action", "omitted-action", "excessive-mutation"],
)
async def test_verification_concern_blocks_before_execution(
    ctx, verification: PlanVerificationDecision
) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_safe_project_plan())
    decisions = _FakeDecisionProvider(
        verification=verification
    )

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=DecisionPolicy(route_enabled=False),
        )
        response = await service.handle(
            user, "Create project Verified One and then create project Verified Two"
        )
        projects = await service._projects.list_projects(user)

    assert response.executed is False
    assert "possible mismatch" in response.reply
    assert projects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
async def test_ambiguous_verification_obeys_required_policy(
    ctx, required: bool
) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_safe_project_plan())
    decisions = _FakeDecisionProvider(
        verification=PlanVerificationDecision(0.45, 0.40, 0.40, 0.55)
    )

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=DecisionPolicy(
                route_enabled=False,
                plan_verification_required=required,
            ),
        )
        response = await service.handle(
            user, "Create project Verified One and then create project Verified Two"
        )
        projects = await service._projects.list_projects(user)

    assert response.executed is (not required)
    assert len(projects) == (0 if required else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
async def test_verification_unavailable_obeys_required_policy(ctx, required: bool) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    understanding = _FakeUnderstandingProvider(_safe_project_plan())
    decisions = _FakeDecisionProvider(
        verification=DecisionProviderError("verification unavailable")
    )

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        service = ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=DecisionPolicy(
                route_enabled=False,
                plan_verification_required=required,
            ),
        )
        response = await service.handle(
            user, "Create project Verified One and then create project Verified Two"
        )
        projects = await service._projects.list_projects(user)

    assert response.executed is (not required)
    assert len(projects) == (0 if required else 2)


@pytest.mark.asyncio
async def test_jev_cannot_approve_invalid_rocky_plan(ctx) -> None:
    client, sessionmaker = ctx
    _, user_id = await _auth_headers(client, sessionmaker)
    invalid = PlanProposal(kind="plan", plan=ProposedPlan(steps=[
        _proposed_plan_step(1, "system.delete_everything"),
        _proposed_plan_step(2, "project.create", {"name": "Never"}),
    ]))
    understanding = _FakeUnderstandingProvider(invalid)
    decisions = _FakeDecisionProvider()

    async with sessionmaker() as session:
        user = await session.get(User, uuid.UUID(user_id))
        assert user is not None
        response = await ConversationService(
            session,
            understanding_provider=understanding,
            decision_provider=decisions,
            decision_policy=DecisionPolicy(route_enabled=False),
        ).handle(user, "Delete everything and create Never")

    assert response.executed is False
    assert "safe plan" in response.reply
    assert decisions.verification_calls == []


# --------------------------------------------------------------------------
# CI-2 durable threads and bounded context.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_thread_and_turns_persist_across_requests(ctx) -> None:
    provider = _use_fake_provider(
        ConversationTurn(kind="conversation", reply="A durable reply.")
    )
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)

    first = await client.post(
        "/conversation", json={"message": "Hello Rocky"}, headers=headers
    )
    second = await client.post(
        "/conversation", json={"message": "Hello again"}, headers=headers
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["thread_id"] == second.json()["thread_id"]
    assert len(provider.calls) == 2
    async with sessionmaker() as session:
        thread = await session.scalar(
            select(ConversationThread).where(
                ConversationThread.user_id == uuid.UUID(user_id),
                ConversationThread.default_key == "default",
            )
        )
        assert thread is not None
        turns = (
            await session.scalars(
                select(ConversationTurnRecord).where(
                    ConversationTurnRecord.thread_id == thread.id
                )
            )
        ).all()
        assert [turn.role for turn in turns] == [
            "user", "assistant", "user", "assistant"
        ]


@pytest.mark.asyncio
async def test_thread_ownership_is_not_disclosed(ctx) -> None:
    client, sessionmaker = ctx
    owner_headers, _ = await _auth_headers(client, sessionmaker)
    other_headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/conversation/threads", json={"title": "Private"}, headers=owner_headers
    )
    assert created.status_code == 201, created.text

    response = await client.post(
        "/conversation",
        json={"message": "Hello", "thread_id": created.json()["id"]},
        headers=other_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation thread not found."


@pytest.mark.asyncio
async def test_threads_isolate_context_for_same_user(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    first_thread = await client.post(
        "/conversation/threads", json={"title": "First"}, headers=headers
    )
    second_thread = await client.post(
        "/conversation/threads", json={"title": "Second"}, headers=headers
    )
    first_id = first_thread.json()["id"]
    second_id = second_thread.json()["id"]

    created = await client.post(
        "/conversation",
        json={"message": "Create a project called Japan", "thread_id": first_id},
        headers=headers,
    )
    assert created.json()["executed"] is True

    isolated = await client.post(
        "/conversation",
        json={"message": "Create a task called Book flights", "thread_id": second_id},
        headers=headers,
    )
    assert isolated.status_code == 200
    assert isolated.json()["executed"] is False
    assert "project" in isolated.json()["reply"].lower()

    continued = await client.post(
        "/conversation",
        json={"message": "Create a task called Book flights", "thread_id": first_id},
        headers=headers,
    )
    assert continued.json()["executed"] is True
    assert "Japan" in continued.json()["reply"]


@pytest.mark.asyncio
async def test_durable_reminder_reference_is_revalidated(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)
    created = await client.post(
        "/conversation",
        json={"message": "Remind me tomorrow at 7 PM to call Rahul"},
        headers=headers,
    )
    assert created.json()["executed"] is True

    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="reminder.cancel",
            reference="that reminder",
        )
    )
    cancelled = await client.post(
        "/conversation",
        json={"message": "Cancel that reminder"},
        headers=headers,
    )
    assert cancelled.json()["executed"] is True
    assert "cancelled" in cancelled.json()["reply"]

    stale = await client.post(
        "/conversation",
        json={"message": "Cancel that reminder"},
        headers=headers,
    )
    assert stale.json()["executed"] is False


@pytest.mark.asyncio
async def test_note_and_list_references_survive_service_instances(ctx) -> None:
    client, sessionmaker = ctx
    headers, _ = await _auth_headers(client, sessionmaker)

    note = await client.post(
        "/conversation",
        json={"message": "Create a note called Launch ideas"},
        headers=headers,
    )
    assert note.json()["executed"] is True
    _use_fake_provider(
        ActionProposal(kind="action", action="note.archive", reference="that note")
    )
    archived = await client.post(
        "/conversation", json={"message": "Archive that note"}, headers=headers
    )
    assert archived.json()["executed"] is True

    created_list = await client.post(
        "/conversation",
        json={"message": "Create a groceries list"},
        headers=headers,
    )
    assert created_list.json()["executed"] is True
    _use_fake_provider(
        ActionProposal(
            kind="action",
            action="list.add_item",
            reference="that list",
            arguments={"content": "milk"},
        )
    )
    added = await client.post(
        "/conversation", json={"message": "Add milk to that list"}, headers=headers
    )
    assert added.json()["executed"] is True
    assert "milk" in added.json()["reply"]


@pytest.mark.asyncio
async def test_context_store_bounds_retained_and_prompt_turns(ctx) -> None:
    client, sessionmaker = ctx
    headers, user_id = await _auth_headers(client, sessionmaker)
    assert headers
    async with sessionmaker() as session:
        store = ConversationContextStore(session)
        thread = await store.resolve_thread(uuid.UUID(user_id), None)
        for index in range(MAX_STORED_TURNS + 5):
            await store.add_turn(
                thread,
                role="user" if index % 2 == 0 else "assistant",
                content=f"turn-{index}",
                language="en",
            )
        stored = (
            await session.scalars(
                select(ConversationTurnRecord).where(
                    ConversationTurnRecord.thread_id == thread.id
                )
            )
        ).all()
        recent = await store.recent_turns(thread.id)

    assert len(stored) == MAX_STORED_TURNS
    assert len(recent) == RECENT_TURN_LIMIT
    assert recent[-1].content == f"turn-{MAX_STORED_TURNS + 4}"


# A stub conforming to the Resolver Protocol, asserted structurally.
_PROTOCOL_CHECK: Resolver = _RogueResolver()
