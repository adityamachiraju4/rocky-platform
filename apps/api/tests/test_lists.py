from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.activity import Activity
from app.models.user import User
import app.models  # noqa: F401

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", "pepper")


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
            yield client, maker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def auth(client, maker):
    email = f"list-{uuid.uuid4().hex[:8]}@example.com"
    created = await client.post("/identity/users", json={"email": email, "password": PASSWORD})
    user_id = uuid.UUID(created.json()["id"])
    async with maker() as session:
        await session.execute(update(User).where(User.id == user_id).values(is_verified=True))
        await session.commit()
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, user_id


@pytest.mark.asyncio
async def test_list_crud_archive_filter_and_activity(ctx):
    client, maker = ctx
    headers, user_id = await auth(client, maker)
    created = await client.post("/lists", json={"title": "Packing"}, headers=headers)
    assert created.status_code == 201
    list_id = created.json()["id"]
    renamed = await client.patch(f"/lists/{list_id}", json={"title": "Trip packing"}, headers=headers)
    assert renamed.json()["title"] == "Trip packing"
    assert [x["id"] for x in (await client.get("/lists", headers=headers)).json()] == [list_id]
    archived = await client.patch(f"/lists/{list_id}", json={"status": "archived"}, headers=headers)
    assert archived.json()["archived_at"] is not None
    repeated = await client.patch(f"/lists/{list_id}", json={"status": "archived"}, headers=headers)
    assert repeated.json()["archived_at"] == archived.json()["archived_at"]
    assert (await client.get("/lists", headers=headers)).json() == []
    assert [x["id"] for x in (await client.get("/lists?status=archived", headers=headers)).json()] == [list_id]
    assert (await client.patch(f"/lists/{list_id}", json={"title": "No"}, headers=headers)).status_code == 409
    async with maker() as session:
        events = list((await session.execute(select(Activity).where(Activity.user_id == user_id))).scalars())
        assert [e.event_type for e in events if e.event_type.startswith("list.")] == ["list.created", "list.archived"]


@pytest.mark.asyncio
async def test_items_append_order_update_complete_and_activity(ctx):
    client, maker = ctx
    headers, _ = await auth(client, maker)
    parent = await client.post("/lists", json={"title": "Grocery"}, headers=headers)
    list_id = parent.json()["id"]
    milk = await client.post(f"/lists/{list_id}/items", json={"content": "milk"}, headers=headers)
    eggs = await client.post(f"/lists/{list_id}/items", json={"content": "eggs"}, headers=headers)
    assert [x["position"] for x in (await client.get(f"/lists/{list_id}/items", headers=headers)).json()] == [0, 1]
    item_id = milk.json()["id"]
    updated = await client.patch(f"/lists/{list_id}/items/{item_id}", json={"content": "oat milk"}, headers=headers)
    assert updated.json()["content"] == "oat milk"
    complete = await client.patch(f"/lists/{list_id}/items/{item_id}", json={"status": "complete"}, headers=headers)
    assert complete.json()["completed_at"] is not None
    repeated = await client.patch(f"/lists/{list_id}/items/{item_id}", json={"status": "complete"}, headers=headers)
    assert repeated.json()["completed_at"] == complete.json()["completed_at"]
    assert [x["id"] for x in (await client.get(f"/lists/{list_id}/items?status=complete", headers=headers)).json()] == [item_id]
    assert (await client.patch(f"/lists/{list_id}/items/{item_id}", json={"content": "no"}, headers=headers)).status_code == 409
    activity = await client.get("/activity", headers=headers)
    types = [e["event_type"] for e in activity.json()]
    assert "list.item_added" in types and "list.item_completed" in types


@pytest.mark.asyncio
async def test_nested_ownership_and_missing_resources_return_404(ctx):
    client, maker = ctx
    mine, _ = await auth(client, maker)
    theirs, _ = await auth(client, maker)
    parent = await client.post("/lists", json={"title": "Private"}, headers=mine)
    list_id = parent.json()["id"]
    item = await client.post(f"/lists/{list_id}/items", json={"content": "secret"}, headers=mine)
    assert (await client.get(f"/lists/{list_id}", headers=theirs)).status_code == 404
    assert (await client.get(f"/lists/{list_id}/items/{item.json()['id']}", headers=theirs)).status_code == 404
    assert (await client.get(f"/lists/{uuid.uuid4()}", headers=mine)).status_code == 404


@pytest.mark.asyncio
async def test_archived_parent_refuses_item_mutation(ctx):
    client, maker = ctx
    headers, _ = await auth(client, maker)
    parent = await client.post("/lists", json={"title": "Done"}, headers=headers)
    list_id = parent.json()["id"]
    item = await client.post(f"/lists/{list_id}/items", json={"content": "one"}, headers=headers)
    await client.patch(f"/lists/{list_id}", json={"status": "archived"}, headers=headers)
    assert (await client.post(f"/lists/{list_id}/items", json={"content": "two"}, headers=headers)).status_code == 409
    assert (await client.patch(f"/lists/{list_id}/items/{item.json()['id']}", json={"status": "complete"}, headers=headers)).status_code == 409
