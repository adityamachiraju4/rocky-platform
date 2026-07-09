"""Alembic runtime environment for Project Rocky.

Integrates with the existing architecture:
  * imports the side-effect-free DeclarativeBase from app.db.base
  * imports app.models so every ORM model registers on Base.metadata
  * reuses the application's database URL from app.core.settings
  * supports offline and online migrations
  * async-engine aware (SQLAlchemy 2.0 + asyncpg/aiosqlite)
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# --- Application integration -------------------------------------------------
# Importing Base must NOT construct an engine (import-time purity is enforced
# by app.db.base). Importing app.models registers all tables on Base.metadata.
from app.db.base import Base
import app.models  # noqa: F401  (side effect: model registration)
from app.db.session import get_database_url

# Alembic Config object, providing access to values in alembic.ini.
config = context.config

# Configure logging from the ini file, if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate / comparison target.
target_metadata = Base.metadata


def get_url() -> str:
    """Single source of truth for the migration URL.

    Reuses the application's settings so database configuration is never
    duplicated. An explicit -x db_url=... override is honored for ad-hoc runs.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("db_url") or get_database_url()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no live DBAPI connection)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations within a real connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
