"""Activity capability API tests (emission-as-side-effect, read scoping,
payload correctness, read-only surface).

Hermetic: in-memory SQLite (aiosqlite), schema from Base.metadata, Platform
get_session overridden. Mirrors tests/test_tasks.py.

Activity is never created through a public endpoint — it is emitted by the
Projects and Tasks domain services within their own transaction. These tests
drive the domain endpoints and then assert on the resulting activity ledger:

* **Emission.** Creating a project emits ``project.created``; creating a task
  emits ``task.created``; completing a task emits ``task.completed`` carrying
  the status transition; a no-op PATCH emits nothing.
* **Read scoping.** A user sees only their own activity. Another user's
  activity is unreachable — 404 when addressed by id, empty when listed.
* **Read-only.** There is no public create path.
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


async def _activity(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> list[dict]:
    resp = await client.get("/activity", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Auth required
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_activity_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.get("/activity")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_activity_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.get(f"/activity/{uuid.uuid4()}")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Emission as a side effect of domain actions
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_project_emits_created(ctx) -> None:
    client, sm = ctx
    headers, uid = await _auth_headers(client, sm)
    pid = await _make_project(client, headers, "Launch")
    acts = await _activity(client, headers)
    assert len(acts) == 1
    ev = acts[0]
    assert ev["event_type"] == "project.created"
    assert ev["entity_type"] == "project"
    assert ev["entity_id"] == pid
    assert ev["user_id"] == uid
    assert ev["payload"]["status"] == "active"


@pytest.mark.asyncio
async def test_create_task_emits_created(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    await _make_task(client, headers, pid, "Ship")
    acts = await _activity(client, headers)
    assert sorted(a["event_type"] for a in acts) == [
        "project.created",
        "task.created",
    ]


@pytest.mark.asyncio
async def test_born_complete_task_emits_only_created(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    await _make_task(client, headers, pid, "Done", status="complete")
    acts = await _activity(client, headers)
    ets = [a["event_type"] for a in acts]
    assert "task.created" in ets
    assert "task.completed" not in ets
    created = next(a for a in acts if a["event_type"] == "task.created")
    assert created["payload"]["status"] == "complete"


@pytest.mark.asyncio
async def test_complete_task_emits_completed_with_transition(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "T"))["id"]
    await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "complete"},
        headers=headers,
    )
    acts = await _activity(client, headers)
    completed = [a for a in acts if a["event_type"] == "task.completed"]
    assert len(completed) == 1
    payload = completed[0]["payload"]
    assert payload["old_status"] == "active"
    assert payload["new_status"] == "complete"
    assert "status" in payload["changed_fields"]


@pytest.mark.asyncio
async def test_reopen_emits_updated_not_reopened(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "T"))["id"]
    await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "complete"},
        headers=headers,
    )
    await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "active"},
        headers=headers,
    )
    acts = await _activity(client, headers)
    ets = [a["event_type"] for a in acts]
    assert ets.count("task.completed") == 1
    assert ets.count("task.updated") == 1


@pytest.mark.asyncio
async def test_title_change_emits_updated(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Old"))["id"]
    await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "New"},
        headers=headers,
    )
    acts = await _activity(client, headers)
    updated = next(a for a in acts if a["event_type"] == "task.updated")
    assert updated["payload"]["changed_fields"] == ["title"]


@pytest.mark.asyncio
async def test_complete_with_title_records_full_delta(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Old"))["id"]
    await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "New", "status": "complete"},
        headers=headers,
    )
    acts = await _activity(client, headers)
    completed = next(a for a in acts if a["event_type"] == "task.completed")
    assert set(completed["payload"]["changed_fields"]) == {"title", "status"}
    assert completed["payload"]["new_status"] == "complete"


@pytest.mark.asyncio
async def test_noop_patch_emits_nothing(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Same"))["id"]
    before = len(await _activity(client, headers))
    resp = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "Same"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    after = len(await _activity(client, headers))
    assert before == after


@pytest.mark.asyncio
async def test_project_update_emits_updated(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers, "Orig")
    await client.patch(
        f"/projects/{pid}", json={"name": "Renamed"}, headers=headers
    )
    acts = await _activity(client, headers)
    updated = next(a for a in acts if a["event_type"] == "project.updated")
    assert updated["payload"]["changed_fields"] == ["name"]


# --------------------------------------------------------------------------- #
# Ordering (newest first)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_activity_ordered_newest_first(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    await _make_task(client, headers, pid, "T")
    acts = await _activity(client, headers)
    # Both events are present. project.created and task.created can share
    # an identical func.now() timestamp (same-transaction granularity), so
    # their relative order is not guaranteed; assert the ledger's actual
    # contract instead: created_at is non-increasing (newest-first).
    assert {a["event_type"] for a in acts} == {
        "project.created",
        "task.created",
    }
    stamps = [a["created_at"] for a in acts]
    assert stamps == sorted(stamps, reverse=True)


# --------------------------------------------------------------------------- #
# Read scoping (the point of the sprint)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_user_sees_only_own_activity(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    await _make_project(client, alice, "Secret")
    assert len(await _activity(client, alice)) == 1
    assert await _activity(client, bob) == []


@pytest.mark.asyncio
async def test_other_user_activity_unreachable_by_id(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    await _make_project(client, alice, "Secret")
    aid = (await _activity(client, alice))[0]["id"]
    mine = await client.get(f"/activity/{aid}", headers=alice)
    theirs = await client.get(f"/activity/{aid}", headers=bob)
    assert mine.status_code == 200
    assert theirs.status_code == 404


# --------------------------------------------------------------------------- #
# Read-only surface: no public create path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_activity_has_no_public_create(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    r1 = await client.post("/activity", json={}, headers=headers)
    r2 = await client.post(f"/activity/{uuid.uuid4()}", json={}, headers=headers)
    assert r1.status_code in (404, 405)
    assert r2.status_code in (404, 405)
