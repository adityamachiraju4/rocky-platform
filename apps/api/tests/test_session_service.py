"""Unit tests for app.services.session_service.

The ``AsyncSession`` is fully mocked — no real database. The mock mirrors
the real shape: ``add`` is sync, ``flush`` / ``commit`` / ``refresh`` are
awaited, and ``execute`` is awaited and returns a result whose
``scalar_one_or_none`` is synchronous.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.refresh_token import RefreshToken
from app.models.session import Session
from app.services.session_service import (
    CreatedSession,
    SessionNotFoundError,
    SessionService,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_db(found_session=None) -> MagicMock:
    """Mock AsyncSession.

    ``add`` records objects; ``flush`` assigns ids to added rows that lack
    one; ``execute().scalar_one_or_none()`` returns ``found_session``.
    """
    db = MagicMock()
    added: list = []

    def _add(obj):
        added.append(obj)

    async def _flush():
        for obj in added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=found_session)

    db.add = MagicMock(side_effect=_add)
    db.flush = AsyncMock(side_effect=_flush)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db._added = added  # exposed for assertions
    return db


def _make_session_row(
    *,
    revoked: bool = False,
    expires_in: timedelta = timedelta(days=1),
    is_trusted: bool = False,
):
    """A lightweight stand-in for a persisted Session row.

    Uses a MagicMock with ``spec=Session`` so it carries the right
    attribute surface without requiring ORM instrumentation.
    """
    s = MagicMock(spec=Session)
    s.id = uuid.uuid4()
    s.user_id = uuid.uuid4()
    s.device_id = uuid.uuid4()
    s.created_at = _now()
    s.last_activity = _now()
    s.expires_at = _now() + expires_in
    s.revoked_at = _now() if revoked else None
    s.is_trusted = is_trusted
    s.session_version = 1
    return s


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_session_returns_session_and_plaintext() -> None:
    db = _make_db()
    service = SessionService(db)

    result = await service.create_session(
        user_id=uuid.uuid4(), device_id=uuid.uuid4()
    )

    assert isinstance(result, CreatedSession)
    assert isinstance(result.session, Session)
    assert isinstance(result.refresh_token, str)
    assert result.refresh_token  # non-empty plaintext
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_create_trusted_session() -> None:
    db = _make_db()
    service = SessionService(db)

    result = await service.create_session(
        user_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        is_trusted=True,
    )
    assert result.session.is_trusted is True
    assert result.session.session_version == 1


# --------------------------------------------------------------------------- #
# Refresh token hashing / no plaintext persistence
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refresh_token_is_hashed_before_storage() -> None:
    db = _make_db()
    service = SessionService(db)

    result = await service.create_session(
        user_id=uuid.uuid4(), device_id=uuid.uuid4()
    )

    stored = [o for o in db._added if isinstance(o, RefreshToken)]
    assert len(stored) == 1
    token_row = stored[0]
    expected = hashlib.sha256(
        result.refresh_token.encode("utf-8")
    ).hexdigest()
    assert token_row.token_hash == expected


@pytest.mark.asyncio
async def test_plaintext_refresh_token_never_persisted() -> None:
    db = _make_db()
    service = SessionService(db)

    result = await service.create_session(
        user_id=uuid.uuid4(), device_id=uuid.uuid4()
    )
    plaintext = result.refresh_token

    stored = [o for o in db._added if isinstance(o, RefreshToken)]
    token_row = stored[0]
    # The plaintext must not appear in any persisted attribute.
    assert token_row.token_hash != plaintext
    assert plaintext not in token_row.token_hash
    # And the hash must not be a trivial transform that leaks length, etc.
    assert len(token_row.token_hash) == 64  # sha256 hex


# --------------------------------------------------------------------------- #
# Get
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_session_found() -> None:
    row = _make_session_row()
    service = SessionService(_make_db(found_session=row))
    got = await service.get_session(row.id)
    assert got is row


@pytest.mark.asyncio
async def test_get_session_missing_returns_none() -> None:
    service = SessionService(_make_db(found_session=None))
    assert await service.get_session(uuid.uuid4()) is None


# --------------------------------------------------------------------------- #
# Revoke
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_revoke_session_sets_revoked_at() -> None:
    row = _make_session_row()
    db = _make_db(found_session=row)
    service = SessionService(db)

    revoked = await service.revoke_session(row.id)
    assert revoked.revoked_at is not None
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_revoke_missing_session_raises() -> None:
    service = SessionService(_make_db(found_session=None))
    with pytest.raises(SessionNotFoundError):
        await service.revoke_session(uuid.uuid4())


@pytest.mark.asyncio
async def test_revoke_is_idempotent() -> None:
    row = _make_session_row(revoked=True)
    original = row.revoked_at
    service = SessionService(_make_db(found_session=row))
    revoked = await service.revoke_session(row.id)
    assert revoked.revoked_at == original


# --------------------------------------------------------------------------- #
# Activity update
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_last_activity() -> None:
    row = _make_session_row()
    old = row.last_activity - timedelta(hours=1)
    row.last_activity = old
    db = _make_db(found_session=row)
    service = SessionService(db)

    updated = await service.update_last_activity(row.id)
    assert updated.last_activity > old
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_update_last_activity_missing_raises() -> None:
    service = SessionService(_make_db(found_session=None))
    with pytest.raises(SessionNotFoundError):
        await service.update_last_activity(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Active checks
# --------------------------------------------------------------------------- #
def test_active_session_is_active() -> None:
    row = _make_session_row(expires_in=timedelta(days=1))
    assert SessionService.is_session_active(row) is True


# --------------------------------------------------------------------------- #
# Configured TTL (expiry policy lives in settings, not the capability)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_session_uses_configured_session_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch the settings getter the service imports, proving the capability
    # consumes configured policy rather than a hard-coded constant.
    import app.services.session_service as svc_mod

    monkeypatch.setattr(
        svc_mod, "get_session_ttl", lambda: timedelta(days=3)
    )
    db = _make_db()
    service = SessionService(db)
    before = _now()
    result = await service.create_session(
        user_id=uuid.uuid4(), device_id=uuid.uuid4()
    )
    delta = result.session.expires_at - before
    assert timedelta(days=2, hours=23) < delta <= timedelta(days=3, minutes=1)


@pytest.mark.asyncio
async def test_create_session_respects_explicit_ttl_override() -> None:
    db = _make_db()
    service = SessionService(db)
    before = _now()
    result = await service.create_session(
        user_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        session_ttl=timedelta(hours=1),
    )
    delta = result.session.expires_at - before
    assert timedelta(minutes=59) < delta <= timedelta(hours=1, minutes=1)


def test_revoked_session_is_inactive() -> None:
    row = _make_session_row(revoked=True)
    assert SessionService.is_session_active(row) is False


def test_expired_session_is_inactive() -> None:
    row = _make_session_row(expires_in=timedelta(seconds=-1))
    assert SessionService.is_session_active(row) is False
