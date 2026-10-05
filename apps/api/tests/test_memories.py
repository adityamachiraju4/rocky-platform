"""Owned explicit memory API, lifecycle, provenance and bounded contracts."""
from datetime import datetime, timedelta, timezone
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql

from test_notes import ctx, auth, _env  # noqa: F401 - reuse the native-domain harness
from app.memories.schemas import MEMORY_CONTENT_MAX, MemoryCreate, MemoryUpdate
from app.memories.repository import MemoryRepository
from app.memories.service import MemoriesService
from app.memories.exceptions import InvalidMemoryTransitionError, MemoryNotFoundError
from app.models import Memory, User, Activity, ConversationThread

PAYLOAD = {"kind": "preference", "subject": "Answer style", "content": "Use concise answers."}


async def create(client, headers, **changes):
    result = await client.post("/memories", headers=headers, json={**PAYLOAD, **changes})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.asyncio
async def test_memory_endpoints_require_auth(ctx):
    client, _ = ctx
    memory_id = uuid.uuid4()
    for method, path, payload in [
        ("GET", "/memories", None), ("POST", "/memories", PAYLOAD),
        ("GET", f"/memories/{memory_id}", None),
        ("PATCH", f"/memories/{memory_id}", {"status": "forgotten"}),
    ]:
        assert (await client.request(method, path, json=payload)).status_code == 401


@pytest.mark.asyncio
async def test_create_owned_active_explicit_memory_with_private_activity(ctx):
    client, maker = ctx
    headers, user_id = await auth(client, maker)
    memory = await create(client, headers)
    assert memory["user_id"] == str(user_id)
    assert memory["status"] == "active" and memory["source"] == "explicit_user"
    assert memory["source_thread_id"] is None and memory["forgotten_at"] is None
    async with maker() as session:
        event = await session.scalar(select(Activity).where(Activity.entity_id == uuid.UUID(memory["id"])))
    assert event.event_type == "memory.remembered"
    assert event.payload == {"kind": "preference", "subject": "Answer style"}
    assert PAYLOAD["content"] not in str(event.payload)


@pytest.mark.asyncio
async def test_listing_owned_active_and_deterministic_updated_order(ctx):
    client, maker = ctx
    owner, _ = await auth(client, maker)
    other, _ = await auth(client, maker)
    first = await create(client, owner, subject="First")
    second = await create(client, owner, subject="Second")
    await create(client, other, subject="Hidden")
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    async with maker() as session:
        for value, age in [(first, 2), (second, 1)]:
            await session.execute(update(Memory).where(Memory.id == uuid.UUID(value["id"])).values(updated_at=now-timedelta(days=age)))
        await session.commit()
    assert [m["subject"] for m in (await client.get("/memories", headers=owner)).json()] == ["Second", "First"]
    await client.patch(f"/memories/{second['id']}", headers=owner, json={"status": "forgotten"})
    assert [m["subject"] for m in (await client.get("/memories", headers=owner)).json()] == ["First"]
    assert [m["subject"] for m in (await client.get("/memories?status=forgotten", headers=owner)).json()] == ["Second"]
    assert (await client.get("/memories?status=invalid", headers=owner)).status_code == 422


@pytest.mark.asyncio
async def test_cross_owner_reads_updates_and_forget_are_not_found(ctx):
    client, maker = ctx
    owner, _ = await auth(client, maker)
    other, _ = await auth(client, maker)
    memory = await create(client, owner)
    path = f"/memories/{memory['id']}"
    assert (await client.get(path, headers=other)).status_code == 404
    for payload in ({"content": "Stolen"}, {"status": "forgotten"}):
        assert (await client.patch(path, headers=other, json=payload)).status_code == 404
    assert (await client.get(path, headers=owner)).json()["content"] == PAYLOAD["content"]


@pytest.mark.asyncio
async def test_mutations_use_locking_owned_lookup(ctx, monkeypatch):
    client, maker = ctx
    headers, user_id = await auth(client, maker)
    created = await create(client, headers)
    memory_id = uuid.UUID(created["id"])
    async with maker() as session:
        user = await session.get(User, user_id)
        service = MemoriesService(session)
        locking_lookup = AsyncMock(wraps=service._memories.get_owned_for_update)
        normal_lookup = AsyncMock(side_effect=AssertionError("Mutation used normal lookup"))
        monkeypatch.setattr(service._memories, "get_owned_for_update", locking_lookup)
        monkeypatch.setattr(service._memories, "get_owned", normal_lookup)

        updated = await service.update_memory(user, memory_id, MemoryUpdate(content="Short answers."))
        assert updated.content == "Short answers."
        forgotten = await service.forget(user, memory_id)
        forgotten_at = forgotten.forgotten_at
        repeated = await service.forget(user, memory_id)
        assert repeated.forgotten_at == forgotten_at
        with pytest.raises(InvalidMemoryTransitionError):
            await service.update_memory(user, memory_id, MemoryUpdate(content="Revive"))
        assert forgotten.content == "Short answers." and forgotten.status == "forgotten"
        missing_id = uuid.uuid4()
        with pytest.raises(MemoryNotFoundError, match="Memory not found"):
            await service.update_memory(user, missing_id, MemoryUpdate(content="Missing"))

        assert locking_lookup.await_args_list == [
            ((user_id, memory_id), {}) for _ in range(4)
        ] + [((user_id, missing_id), {})]
        normal_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_locking_owned_lookup_compiles_with_postgresql_row_lock():
    session = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    user_id, memory_id = uuid.uuid4(), uuid.uuid4()

    assert await MemoryRepository(session).get_owned_for_update(user_id, memory_id) is None

    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.endswith("FOR UPDATE")
    assert "memories.id =" in sql and "memories.user_id =" in sql
    assert set(compiled.params.values()) == {memory_id, user_id}


@pytest.mark.asyncio
async def test_update_event_only_when_changed_and_forgotten_is_final(ctx):
    client, maker = ctx
    headers, _ = await auth(client, maker)
    memory = await create(client, headers)
    path = f"/memories/{memory['id']}"
    for _ in range(2):
        assert (await client.patch(path, headers=headers, json={"content": "Keep answers short."})).status_code == 200
    first_forget = await client.patch(path, headers=headers, json={"status": "forgotten"})
    second_forget = await client.patch(path, headers=headers, json={"status": "forgotten"})
    assert first_forget.json()["forgotten_at"] is not None
    assert first_forget.json() == second_forget.json()
    assert (await client.patch(path, headers=headers, json={"content": "Revive"})).status_code == 409
    assert (await client.patch(path, headers=headers, json={"status": "active"})).status_code == 422
    async with maker() as session:
        events = (await session.scalars(select(Activity).where(Activity.entity_id == uuid.UUID(memory["id"])).order_by(Activity.created_at))).all()
    assert [e.event_type for e in events] == ["memory.remembered", "memory.updated", "memory.forgotten"]
    assert all("content" not in e.payload for e in events)
    new = await create(client, headers)
    assert new["id"] != memory["id"] and new["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"kind": None}, {"subject": None}, {"content": None}, {"status": None},
    {"subject": " "}, {"content": ""}, {"kind": "inferred"}, {"status": "archived"},
    {"subject": "x" * 256}, {"content": "x" * (MEMORY_CONTENT_MAX+1)},
    {"source": "explicit_user"}, {"user_id": str(uuid.uuid4())},
    {"source_thread_id": str(uuid.uuid4())}, {"created_at": "2026-10-01"},
])
async def test_invalid_memory_updates_are_rejected(ctx, payload):
    client, maker = ctx
    headers, _ = await auth(client, maker)
    memory = await create(client, headers)
    assert (await client.patch(f"/memories/{memory['id']}", headers=headers, json=payload)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"kind": "inferred"}, {"subject": " "}, {"content": " "},
    {"content": "x" * 2001}, {"subject": "x" * 256},
    {"source": "explicit_user"}, {"user_id": str(uuid.uuid4())},
    {"source_thread_id": str(uuid.uuid4())}, {"status": "active"}, {"id": str(uuid.uuid4())},
])
async def test_create_contract_rejects_invalid_or_authoritative_fields(ctx, changes):
    client, maker = ctx
    headers, _ = await auth(client, maker)
    assert (await client.post("/memories", headers=headers, json={**PAYLOAD, **changes})).status_code == 422


@pytest.mark.asyncio
async def test_source_thread_must_be_owned_and_thread_deletion_preserves_memory(ctx):
    client, maker = ctx
    owner_headers, owner_id = await auth(client, maker)
    other_headers, other_id = await auth(client, maker)
    own_thread = (await client.post("/conversation/threads", headers=owner_headers, json={})).json()["id"]
    other_thread = (await client.post("/conversation/threads", headers=other_headers, json={})).json()["id"]
    async with maker() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        user = await session.get(User, owner_id)
        service = MemoriesService(session)
        with pytest.raises(MemoryNotFoundError):
            await service.remember(user, MemoryCreate(**PAYLOAD), source_thread_id=uuid.UUID(other_thread))
        memory = await service.remember(user, MemoryCreate(**PAYLOAD), source_thread_id=uuid.UUID(own_thread))
        memory_id = memory.id
        await session.execute(delete(ConversationThread).where(ConversationThread.id == uuid.UUID(own_thread)))
        await session.commit()
        session.expire_all()
        remaining = await session.get(Memory, memory_id)
        assert remaining is not None and remaining.source_thread_id is None
        assert remaining.user_id == owner_id


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"kind": "inferred"}, {"source": "inferred"}, {"status": "archived"},
    {"status": "forgotten"}, {"content": "x" * 2001}, {"subject": "x" * 256},
    {"subject": ""}, {"content": ""},
])
async def test_memory_database_constraints_are_enforced(ctx, changes):
    client, maker = ctx
    _, user_id = await auth(client, maker)
    async with maker() as session:
        memory = Memory(user_id=user_id, **{**PAYLOAD, **changes})
        session.add(memory)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
