"""Platform-006 request context infrastructure tests.

Covers the sprint's acceptance criteria: request id generation, correlation id
preservation and generation, uniqueness across requests, context isolation
between concurrent requests, cleanup after the response, and continued correct
behavior of the existing /health and /ready probes.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.request_context import (
    get_correlation_id,
    get_request_context,
    get_request_id,
)
from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_every_request_receives_a_request_id() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_incoming_correlation_id_is_preserved() -> None:
    async with _client() as client:
        resp = await client.get(
            "/health", headers={"X-Correlation-ID": "corr-abc-123"}
        )
    assert resp.headers["x-correlation-id"] == "corr-abc-123"


@pytest.mark.asyncio
async def test_correlation_id_generated_when_absent() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    correlation_id = resp.headers.get("x-correlation-id")
    assert correlation_id
    assert len(correlation_id) >= 8


@pytest.mark.asyncio
async def test_consecutive_requests_receive_different_request_ids() -> None:
    async with _client() as client:
        first = await client.get("/health")
        second = await client.get("/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


@pytest.mark.asyncio
async def test_concurrent_requests_have_isolated_context() -> None:
    """Concurrent requests must not observe each other's identifiers."""

    async def one(client: AsyncClient, index: int) -> str:
        correlation_id = f"corr-{index}"
        resp = await client.get(
            "/health", headers={"X-Correlation-ID": correlation_id}
        )
        assert resp.headers["x-correlation-id"] == correlation_id
        return resp.headers["x-request-id"]

    async with _client() as client:
        request_ids = await asyncio.gather(
            *[one(client, i) for i in range(25)]
        )

    assert len(set(request_ids)) == 25


@pytest.mark.asyncio
async def test_context_is_cleared_after_the_request() -> None:
    """Reset tokens must clear the context once the response is sent."""
    async with _client() as client:
        await client.get("/health")

    assert get_request_id() is None
    assert get_correlation_id() is None
    snapshot = get_request_context()
    assert snapshot.request_id is None
    assert snapshot.correlation_id is None


@pytest.mark.asyncio
async def test_health_endpoint_still_behaves_correctly() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_ready_endpoint_still_behaves_correctly() -> None:
    async with _client() as client:
        resp = await client.get("/ready")
    assert resp.status_code in (200, 503)
    assert resp.json()["status"] in ("ready", "not ready")
