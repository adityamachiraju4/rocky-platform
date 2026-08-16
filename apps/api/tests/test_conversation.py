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

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.user import User

import app.models  # noqa: F401  (populate Base.metadata before create_all)

from app.conversation.service import ConversationService
from app.conversation.exceptions import UnknownActionError
from app.conversation.resolver import Resolver, WorldView
from app.conversation.schemas import ResolvedAction

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)


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


# --------------------------------------------------------------------------
# Happy path: the proof loop.
# --------------------------------------------------------------------------


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

    fetched = await _get_task(client, headers, project_id, task["id"])
    assert fetched["status"] == "active"
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
