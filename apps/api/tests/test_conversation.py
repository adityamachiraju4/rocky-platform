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

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
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

import app.models  # noqa: F401  (populate Base.metadata before create_all)

from app.conversation import registry
from app.conversation.service import ConversationService
from app.conversation.exceptions import UnknownActionError
from app.conversation.dependencies import get_understanding_provider
from app.conversation.resolver import Resolver, WorldView
from app.conversation.schemas import ResolvedAction
from app.conversation.understanding import (
    ActionProposal,
    ConversationTurn,
    UNDERSTANDING_JSON_SCHEMA,
    UnderstandingProviderError,
    UnderstandingResult,
    Unsupported,
    safe_world_payload,
)

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


class _FakeUnderstandingProvider:
    def __init__(self, result: object | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def understand(self, **_: object) -> object:
        self.calls.append(_)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _use_fake_provider(result: object | Exception) -> _FakeUnderstandingProvider:
    provider = _FakeUnderstandingProvider(result)
    fastapi_app.dependency_overrides[get_understanding_provider] = (
        lambda: provider
    )
    return provider


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


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
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            yield ac, sessionmaker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        fastapi_app.dependency_overrides.pop(get_understanding_provider, None)
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


# A stub conforming to the Resolver Protocol, asserted structurally.
_PROTOCOL_CHECK: Resolver = _RogueResolver()
