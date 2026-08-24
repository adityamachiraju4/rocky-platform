#!/usr/bin/env bash
# Platform-005: Centralize dependency providers into app/core/dependencies.py.
#
# Movable surface (only pre-existing providers):
#   * get_session                 (was app/db/database.py)
#   * get_identity_service        (was app/identity/dependencies.py)
#   * IdentityServiceDep          (was app/identity/dependencies.py)
#
# Definitions move to app/core/dependencies.py (the composition layer). The two
# original modules become thin re-exports so existing public APIs are preserved
# byte-for-behavior:
#   * app/db/__init__.py still exports get_session (declared public API)
#   * app/identity/router.py's `from .dependencies import IdentityServiceDep`
#     keeps working; router is ALSO repointed at the canonical module per the
#     "update imports to use the centralized module" requirement.
#
# No DI framework / IoC / service locator / registry / reflection / new
# decorators. Native FastAPI Depends() only, unchanged. Object identity across
# all import paths is preserved (validated), so dependency_overrides stay safe.
#
# Untouched by design: app/models/user.py, app/events/, alembic/versions/*.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT/apps/api"

# --- 1. NEW canonical composition module -----------------------------------
cat > app/core/dependencies.py <<'PY'
"""Centralized FastAPI dependency-provider composition layer for Rocky.

This module is the single home for the application's request-scoped
dependency providers. It relocates providers that previously lived in
``app.db.database`` and ``app.identity.dependencies``; those modules now
re-export from here so existing import paths and public APIs are preserved.

Only native FastAPI ``Depends()`` wiring is used — no container, registry,
service locator, or reflection. Providers are plain callables and
``Annotated`` aliases, exactly as before.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker
from app.identity.service import IdentityService


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped :class:`AsyncSession`.

    Intended for use with FastAPI's dependency injection. The session
    factory (and engine) are created lazily on the first request, never at
    import time. The session is closed automatically when the request
    completes; on an unhandled exception the transaction is rolled back
    before the session closes.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_identity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityService:
    """Construct an :class:`IdentityService` bound to the request session."""
    return IdentityService(session)


IdentityServiceDep = Annotated[
    IdentityService, Depends(get_identity_service)
]


__all__ = [
    "get_session",
    "get_identity_service",
    "IdentityServiceDep",
]
PY
echo "Wrote app/core/dependencies.py"

# --- 2. app/db/database.py -> re-export (preserve public API) ---------------
cat > app/db/database.py <<'PY'
"""FastAPI-compatible database session dependency.

The provider is defined in :mod:`app.core.dependencies` (the centralized
composition layer, Platform-005). It is re-exported here so the historical
import path ``app.db.database.get_session`` — and the ``app.db`` package's
declared public API — continue to resolve to the same object.
"""
from __future__ import annotations

from app.core.dependencies import get_session

__all__ = ["get_session"]
PY
echo "Rewrote app/db/database.py as re-export"

# --- 3. app/identity/dependencies.py -> re-export ---------------------------
cat > app/identity/dependencies.py <<'PY'
"""Dependency-injection wiring for the Identity subsystem.

The providers are defined in :mod:`app.core.dependencies` (the centralized
composition layer, Platform-005) and re-exported here so existing imports
(e.g. ``from .dependencies import IdentityServiceDep``) keep working and
resolve to the same objects.
"""
from __future__ import annotations

from app.core.dependencies import get_identity_service, IdentityServiceDep

__all__ = ["get_identity_service", "IdentityServiceDep"]
PY
echo "Rewrote app/identity/dependencies.py as re-export"

# --- 4. Repoint the router at the canonical module -------------------------
python - <<'PY'
from pathlib import Path
p = Path("app/identity/router.py")
src = p.read_text()
old = "from .dependencies import IdentityServiceDep"
new = "from app.core.dependencies import IdentityServiceDep"
if old not in src:
    raise SystemExit(
        "EXPECTED 'from .dependencies import IdentityServiceDep' in router.py "
        "— aborting, repoint by hand."
    )
src = src.replace(old, new, 1)
p.write_text(src)
print("Repointed app/identity/router.py import to app.core.dependencies")
PY

echo "DONE."
