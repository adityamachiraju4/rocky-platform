"""PostgreSQL migration/metadata parity guard.

Every other test in this suite builds schema from Base.metadata on SQLite,
which means a model shipped without a migration still passes green. That is
exactly how the `sessions` / `refresh_tokens` tables reached production
missing, producing a live 500 on login.

This test closes that hole: it runs the real Alembic chain onto a clean
throwaway PostgreSQL database, then asserts the resulting schema has zero
drift against Base.metadata.

Marked `parity` and deselected from the default run, so ordinary local
invocations do not require database-admin privileges. It never skips: when
selected, it either runs or fails loudly.

    pytest -m parity
"""
from __future__ import annotations

import asyncio
import os
import uuid
from argparse import Namespace
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
import app.models  # noqa: F401  (side effect: model registration)

pytestmark = pytest.mark.parity

ASYNC_PREFIX = "postgresql+asyncpg://"
MAINTENANCE_DB = "postgres"


def _require_test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. The migration parity guard requires "
            "an accessible PostgreSQL instance with permission to CREATE and "
            "DROP databases. Set it in .env, e.g.\n"
            "  TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/postgres"
        )
    return url


def _to_async(url: str) -> str:
    if url.startswith(ASYNC_PREFIX):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", ASYNC_PREFIX, 1)
    pytest.fail(
        f"TEST_DATABASE_URL must be a PostgreSQL URL, got: {url.split('://')[0]}://... "
        "SQLite cannot validate the migration chain and is the reason this "
        "guard exists."
    )


def _with_database(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


async def _admin_execute(admin_url: str, statement: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def _collect_diff(scratch_url: str) -> list:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn,
                        opts={"compare_type": True, "compare_server_default": True},
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def _upgrade_to_head(scratch_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.cmd_opts = Namespace(x=[f"db_url={scratch_url}"])
    command.upgrade(cfg, "head")


def test_migrations_match_metadata() -> None:
    base_url = _to_async(_require_test_url())
    scratch_db = f"rocky_parity_{uuid.uuid4().hex[:8]}"
    admin_url = _with_database(base_url, MAINTENANCE_DB)
    scratch_url = _with_database(base_url, scratch_db)

    try:
        asyncio.run(_admin_execute(admin_url, f'CREATE DATABASE "{scratch_db}"'))
    except Exception as exc:
        pytest.fail(
            f"Could not create parity database {scratch_db!r}: {exc}\n"
            "TEST_DATABASE_URL must point to a reachable PostgreSQL instance "
            "whose role has CREATE DATABASE privileges."
        )

    try:
        _upgrade_to_head(scratch_url)
        diff = asyncio.run(_collect_diff(scratch_url))
    finally:
        try:
            asyncio.run(_admin_execute(admin_url, f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        except Exception:
            pass

    if diff:
        rendered = "\n".join(f"  - {item}" for item in diff)
        pytest.fail(
            "Migration chain does not match Base.metadata. Every ORM model must "
            "have corresponding migration DDL.\n"
            f"Drift ({len(diff)} item(s)):\n{rendered}"
        )
