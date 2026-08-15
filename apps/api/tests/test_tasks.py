"""Tasks capability API tests (create / list / get / update / complete / isolation).

Hermetic: in-memory SQLite (aiosqlite), schema from Base.metadata, Platform
get_session overridden. Mirrors tests/test_projects.py.

Two load-bearing assertions:

* **Transitive isolation.** A task is reached only through its owning project.
  Another user's task — and another user's project — is indistinguishable from
  a nonexistent one (404, not 403), so neither can be probed.
* **Completion semantics.** A transition into ``complete`` stamps
  ``completed_at``; a transition back to ``active`` clears it. There is no
  dedicated completion endpoint: PATCH is the single write path.
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
    """Register + verify + login. Returns (headers, user_id)."""
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
) -> dict:
    resp = await client.post(
        f"/projects/{project_id}/tasks",
        json={"title": title},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Authentication is required
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_task_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.post(
        f"/projects/{uuid.uuid4()}/tasks", json={"title": "Nope"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_tasks_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.get(f"/projects/{uuid.uuid4()}/tasks")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_task_under_own_project(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    resp = await client.post(
        f"/projects/{pid}/tasks",
        json={"title": "Ship it", "description": "the whole thing"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Ship it"
    assert body["description"] == "the whole thing"
    assert body["status"] == "active"
    assert body["completed_at"] is None
    assert body["project_id"] == pid


@pytest.mark.asyncio
async def test_create_task_rejects_empty_title(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    resp = await client.post(
        f"/projects/{pid}/tasks", json={"title": ""}, headers=headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_rejects_unknown_status(ctx) -> None:
    """Unrecognized status would desync completed_at - rejected at schema."""
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    resp = await client.post(
        f"/projects/{pid}/tasks",
        json={"title": "T", "status": "banana"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_under_nonexistent_project_404(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    resp = await client.post(
        f"/projects/{uuid.uuid4()}/tasks",
        json={"title": "Orphan"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_task_born_complete_stamps_completed_at(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    resp = await client.post(
        f"/projects/{pid}/tasks",
        json={"title": "Already done", "status": "complete"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "complete"
    assert resp.json()["completed_at"] is not None


# --------------------------------------------------------------------------- #
# List / get / update
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_returns_only_tasks_in_that_project(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    p1 = await _make_project(client, headers, "P1")
    p2 = await _make_project(client, headers, "P2")
    await _make_task(client, headers, p1, "A1")
    await _make_task(client, headers, p1, "A2")
    await _make_task(client, headers, p2, "B1")

    r1 = await client.get(f"/projects/{p1}/tasks", headers=headers)
    r2 = await client.get(f"/projects/{p2}/tasks", headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert sorted(t["title"] for t in r1.json()) == ["A1", "A2"]
    assert [t["title"] for t in r2.json()] == ["B1"]


@pytest.mark.asyncio
async def test_get_own_task(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Mine"))["id"]
    resp = await client.get(f"/projects/{pid}/tasks/{tid}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Mine"


@pytest.mark.asyncio
async def test_update_own_task(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Old"))["id"]
    resp = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "New", "description": "edited"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "New"
    assert resp.json()["description"] == "edited"
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_get_unknown_task_404(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    resp = await client.get(
        f"/projects/{pid}/tasks/{uuid.uuid4()}", headers=headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_task_not_reachable_through_wrong_project(ctx) -> None:
    """Right task id, wrong (but owned) project - still 404."""
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    p1 = await _make_project(client, headers, "P1")
    p2 = await _make_project(client, headers, "P2")
    tid = (await _make_task(client, headers, p1, "InP1"))["id"]
    resp = await client.get(f"/projects/{p2}/tasks/{tid}", headers=headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Completion semantics (single write path, no /complete endpoint)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_complete_then_reopen_round_trip(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "Round trip"))["id"]

    done = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "complete"},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "complete"
    assert done.json()["completed_at"] is not None

    reopened = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "active"},
        headers=headers,
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "active"
    assert reopened.json()["completed_at"] is None


@pytest.mark.asyncio
async def test_unrelated_patch_preserves_completed_at(ctx) -> None:
    """Editing the title of a complete task must not clear completed_at."""
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, headers)
    tid = (await _make_task(client, headers, pid, "T"))["id"]

    done = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "complete"},
        headers=headers,
    )
    stamp = done.json()["completed_at"]
    assert stamp is not None

    renamed = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "Renamed"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Renamed"
    assert renamed.json()["status"] == "complete"
    assert renamed.json()["completed_at"] == stamp


# --------------------------------------------------------------------------- #
# Cross-user isolation (the point of the sprint)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_other_user_cannot_list_tasks_in_my_project(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, alice, "Secret")
    await _make_task(client, alice, pid, "Confidential")

    mine = await client.get(f"/projects/{pid}/tasks", headers=alice)
    theirs = await client.get(f"/projects/{pid}/tasks", headers=bob)
    assert mine.status_code == 200
    # 404, not 403: the project's existence must not be probeable.
    assert theirs.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_create_task_in_my_project(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, alice, "Secret")

    resp = await client.post(
        f"/projects/{pid}/tasks", json={"title": "Injected"}, headers=bob
    )
    assert resp.status_code == 404
    after = await client.get(f"/projects/{pid}/tasks", headers=alice)
    assert after.json() == []


@pytest.mark.asyncio
async def test_other_user_cannot_get_my_task(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, alice, "Secret")
    tid = (await _make_task(client, alice, pid, "Confidential"))["id"]

    mine = await client.get(f"/projects/{pid}/tasks/{tid}", headers=alice)
    theirs = await client.get(f"/projects/{pid}/tasks/{tid}", headers=bob)
    assert mine.status_code == 200
    assert theirs.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_update_my_task(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, alice, "Secret")
    tid = (await _make_task(client, alice, pid, "Confidential"))["id"]

    resp = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"title": "Hijacked"},
        headers=bob,
    )
    assert resp.status_code == 404
    after = await client.get(f"/projects/{pid}/tasks/{tid}", headers=alice)
    assert after.json()["title"] == "Confidential"


@pytest.mark.asyncio
async def test_other_user_cannot_complete_my_task(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = await _make_project(client, alice, "Secret")
    tid = (await _make_task(client, alice, pid, "Confidential"))["id"]

    resp = await client.patch(
        f"/projects/{pid}/tasks/{tid}",
        json={"status": "complete"},
        headers=bob,
    )
    assert resp.status_code == 404
    after = await client.get(f"/projects/{pid}/tasks/{tid}", headers=alice)
    assert after.json()["status"] == "active"
    assert after.json()["completed_at"] is None


@pytest.mark.asyncio
async def test_cross_user_project_id_swap_does_not_leak(ctx) -> None:
    """Bob's own project id + Alice's task id must not resolve."""
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    a_pid = await _make_project(client, alice, "AliceP")
    b_pid = await _make_project(client, bob, "BobP")
    a_tid = (await _make_task(client, alice, a_pid, "AliceT"))["id"]

    resp = await client.get(f"/projects/{b_pid}/tasks/{a_tid}", headers=bob)
    assert resp.status_code == 404
