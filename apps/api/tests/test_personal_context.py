"""CI-6 contract, lexical ranking, owned service calls and bounded payloads."""
from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.conversation.personal_context import (
    MAX_CONTENT_CHARS, MAX_LIST_ITEMS, MAX_PER_SCOPE, MAX_TITLE_CHARS,
    MAX_TOTAL_RECORDS, PersonalContextRetriever,
)
from app.conversation.resolver import NoteRef, WorldView
from app.conversation.understanding import (
    PERSONAL_SCOPES, UNDERSTANDING_JSON_SCHEMA, PersonalContextRequest,
    UnderstandingProviderError, parse_understanding_payload, safe_world_payload,
)


def record(number, **fields):
    return SimpleNamespace(id=uuid.UUID(int=number), **fields)


@pytest.fixture
def services():
    return {scope: SimpleNamespace(**{
        f"list_{scope}": AsyncMock(return_value=[]),
        **({"list_items": AsyncMock(return_value=[])} if scope == "lists" else {}),
    }) for scope in PERSONAL_SCOPES}


def request(query="*", scopes=("notes",)):
    return PersonalContextRequest(kind="personal_context", query=query, scopes=scopes)


def test_valid_contract_and_strict_nested_schema():
    result = parse_understanding_payload({
        "kind": "personal_context", "retrieval": {"query": "PhantomRed pricing", "scopes": ["notes", "lists"]},
    })
    assert result == request("PhantomRed pricing", ("notes", "lists"))
    schema = UNDERSTANDING_JSON_SCHEMA["properties"]["retrieval"]["anyOf"][1]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["scopes"]["items"]["enum"] == list(PERSONAL_SCOPES)


@pytest.mark.parametrize("retrieval", [
    None, {}, {"query": "pricing"}, {"query": "pricing", "scopes": []},
    {"query": "pricing", "scopes": ["secrets"]},
    {"query": "pricing", "scopes": ["notes", "notes"]},
    {"query": "x" * 201, "scopes": ["notes"]},
    {"query": "   ", "scopes": ["notes"]},
    {"query": 42, "scopes": ["notes"]},
    {"query": "pricing", "scopes": "notes"},
    {"query": "pricing", "scopes": ["notes"], "owner_id": "someone"},
])
def test_invalid_contract_fails_closed(retrieval):
    with pytest.raises(UnderstandingProviderError):
        parse_understanding_payload({"kind": "personal_context", "retrieval": retrieval})


def test_retrieval_cannot_attach_to_other_result():
    with pytest.raises(UnderstandingProviderError):
        parse_understanding_payload({"kind": "conversation", "retrieval": {"query": "*", "scopes": ["notes"]}})
    with pytest.raises(UnderstandingProviderError):
        parse_understanding_payload({
            "kind": "personal_context", "action": "project.list",
            "retrieval": {"query": "*", "scopes": ["notes"]},
        })
    assert request("x" * 200).validated().query == "x" * 200


@pytest.mark.asyncio
async def test_invalid_direct_request_does_not_call_services(services):
    with pytest.raises(UnderstandingProviderError):
        await PersonalContextRetriever(**services).retrieve(object(), request(scopes=("secrets",)))
    for scope, service in services.items():
        getattr(service, f"list_{scope}").assert_not_awaited()


@pytest.mark.asyncio
async def test_notes_ranking_ownership_status_and_private_logging(services, caplog):
    user = object()
    values = [
        record(1, title="Unrelated", content="PRIVATE unrelated text", status="active"),
        record(2, title="PhantomRed", content="pricing decision", status="active"),
        record(3, title="PhantomRed pricing", content="PRIVATE decision", status="archived"),
        record(4, title="PhantomRed pricing", content="PRIVATE decision", status="active"),
        record(5, title="Other", content="PhantomRed pricing PRIVATE", status="active"),
    ]
    services["notes"].list_notes.side_effect = lambda user, status: [v for v in values if v.status == status]
    with caplog.at_level(logging.INFO):
        world = await PersonalContextRetriever(**services).retrieve(user, request("PHANTOMRED pricing"))
    assert [n.note_id.int for n in world.notes] == [4, 3, 2, 5]
    assert not world.tasks and not world.projects and not world.lists
    assert services["notes"].list_notes.await_args_list[0].args == (user,)
    assert {c.kwargs["status"] for c in services["notes"].list_notes.await_args_list} == {"active", "archived"}
    for scope in set(PERSONAL_SCOPES) - {"notes"}:
        getattr(services[scope], f"list_{scope}").assert_not_awaited()
    event = next(r for r in caplog.records if r.message == "personal_context_retrieved")
    assert event.selected_counts["notes"] == 4
    assert event.query_specific is True
    assert "PRIVATE" not in str(event.__dict__)
    assert "PHANTOMRED" not in str(event.__dict__)


@pytest.mark.asyncio
async def test_phrase_token_ties_and_order_independent_selection(services):
    values = [record(i, title=title, content="", status="active") for i, title in [
        (5, "alpha other"), (4, "alpha other"), (3, "beta alpha"), (2, "alpha beta extra"), (1, "alpha beta")
    ]]
    async def notes(user, status):
        return list(values) if status == "active" else []
    services["notes"].list_notes.side_effect = notes
    retriever = PersonalContextRetriever(**services)
    first = await retriever.retrieve(object(), request("alpha beta"))
    values.reverse()
    second = await retriever.retrieve(object(), request("alpha beta"))
    assert first == second
    assert [n.note_id.int for n in first.notes] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_task_dependency_does_not_expose_projects(services):
    user = object()
    services["projects"].list_projects.return_value = [record(1, name="PhantomRed")]
    services["tasks"].list_tasks.return_value = [record(2, title="Set pricing", status="active")]
    world = await PersonalContextRetriever(**services).retrieve(user, request("PhantomRed", ("tasks",)))
    assert not world.projects
    assert world.tasks[0].title == "Set pricing"
    services["projects"].list_projects.assert_awaited_once_with(user)
    services["tasks"].list_tasks.assert_awaited_once_with(user, uuid.UUID(int=1))


@pytest.mark.asyncio
async def test_reminder_status_due_time_and_notification_recency(services):
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    services["reminders"].list_reminders.return_value = [
        record(1, title="Follow up", status="completed", due_at=now - timedelta(days=2), timezone="UTC"),
        record(2, title="Follow up", status="scheduled", due_at=now + timedelta(days=1), timezone="UTC"),
        record(3, title="Follow up", status="due", due_at=now, timezone="UTC"),
    ]
    services["notifications"].list_notifications.return_value = [
        record(4, title="Follow up", body="", status="unread", created_at=now),
        record(5, title="Follow up", body="", status="unread", created_at=now + timedelta(hours=1)),
        record(6, title="Follow up", body="", status="read", created_at=now + timedelta(days=1)),
    ]
    world = await PersonalContextRetriever(**services).retrieve(object(), request("follow up", ("reminders", "notifications")))
    assert [r.reminder_id.int for r in world.reminders] == [3, 2, 1]
    assert [n.notification_id.int for n in world.notifications] == [5, 4, 6]


@pytest.mark.asyncio
async def test_list_item_content_matches_and_selected_items_are_bounded(services):
    user = object()
    services["lists"].list_lists.side_effect = lambda user, status: [record(1, title="Decisions", status=status)] if status == "active" else []
    services["lists"].list_items.return_value = [record(i, content="unrelated", status="active") for i in range(2, 20)] + [record(20, content="PhantomRed pricing " + "x" * 2000, status="complete")]
    world = await PersonalContextRetriever(**services).retrieve(user, request("PhantomRed pricing", ("lists",)))
    assert world.lists[0].items[0].item_id.int == 20
    assert len(world.lists[0].items) == MAX_LIST_ITEMS
    assert len(world.lists[0].items[0].content) == MAX_CONTENT_CHARS
    services["lists"].list_items.assert_awaited_once_with(user, uuid.UUID(int=1))


@pytest.mark.asyncio
async def test_all_scope_and_total_bounds_and_deterministic_truncation(services):
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    services["projects"].list_projects.return_value = [record(i, name="p" * 500) for i in range(1, 15)]
    services["tasks"].list_tasks.return_value = [record(i, title="t" * 500, status="active") for i in range(20, 35)]
    services["reminders"].list_reminders.return_value = [record(i, title="r" * 500, status="due", due_at=now, timezone="UTC") for i in range(40, 55)]
    services["notifications"].list_notifications.return_value = [record(i, title="n" * 500, body="b" * 5000, status="unread", created_at=now) for i in range(60, 75)]
    services["notes"].list_notes.side_effect = lambda user, status: [record(i, title="n" * 500, content="c" * 5000, status=status) for i in range(80, 95)] if status == "active" else []
    services["lists"].list_lists.side_effect = lambda user, status: [record(i, title="l" * 500, status=status) for i in range(100, 115)] if status == "active" else []
    services["lists"].list_items.return_value = [record(i, content="i" * 5000, status="active") for i in range(120, 140)]
    world = await PersonalContextRetriever(**services).retrieve(object(), request(scopes=PERSONAL_SCOPES))
    assert sum(len(getattr(world, scope)) for scope in PERSONAL_SCOPES) == MAX_TOTAL_RECORDS
    assert all(len(getattr(world, scope)) == MAX_PER_SCOPE for scope in PERSONAL_SCOPES)
    assert world.projects[0].name == "p" * MAX_TITLE_CHARS
    assert world.notes[0].content == "c" * MAX_CONTENT_CHARS
    assert len(world.lists[0].items) == MAX_LIST_ITEMS
    payload = safe_world_payload(world)
    assert payload["notes"][0]["content"] == "c" * MAX_CONTENT_CHARS
    assert "note_id" not in payload["notes"][0]


def test_serialization_defense_in_depth_for_broad_world():
    world = WorldView(projects=(), tasks=(), notes=tuple(
        NoteRef(uuid.UUID(int=i), "t" * 500, "c" * 5000, "active") for i in range(1, 100)
    ))
    payload = safe_world_payload(world)
    assert len(payload["notes"]) == 50
    assert payload["notes"][0]["title"] == "t" * 200
    assert payload["notes"][0]["content"] == "c" * 1000
