# Identity Core — Sprint 001

Foundational identity subsystem for Project Rocky. No AI functionality is
included in this sprint; this module provides users, preferences, devices,
and sessions that later modules (Memory, Continuity, Planning) build upon.

## Folder Structure

```
apps/api/
    main.py                 FastAPI app entrypoint, mounts the identity router
    database.py             Async engine, sessionmaker, get_session dependency
    alembic.ini             Alembic configuration
    alembic/
        env.py              Async migration environment
        versions/
            0001_identity_core.py   Initial schema migration
    identity/
        __init__.py
        models.py           SQLAlchemy ORM models (User, Preferences, Device, Session)
        schemas.py          Pydantic v2 request/response schemas
        repository.py       Database access only — no business logic
        service.py          Business logic — orchestrates repositories
        router.py           HTTP routing only — no business logic, no SQL
        dependencies.py     Dependency-injection wiring
        exceptions.py       Domain exceptions
        tests/              Model, repository, service, and API tests
```

## Architecture

Request flow follows a strict layering:

```
router  ->  service  ->  repository  ->  database
(HTTP)      (logic)      (persistence)   (async session)
```

- **Routers** translate HTTP to/from the service layer and map domain
  exceptions to HTTP status codes. They contain no business logic and issue
  no SQL.
- **Services** hold all business rules (e.g. uniqueness checks, creating a
  default preferences row on user creation) and own transaction boundaries.
- **Repositories** perform database access only.
- **Dependency injection** supplies an async `AsyncSession` per request via
  `get_session`, wrapped into an `IdentityService`.

Everything is async, strictly typed, and uses Pydantic v2 for validation.

## Model Relationships

```
User 1 ── 1 Preferences        (one-to-one, cascade delete)
User 1 ── * Device             (one-to-many, cascade delete)
User 1 ── * Session            (one-to-many, cascade delete)
Device 1 ── * Session          (optional; session.device_id SET NULL on device delete)
```

### users
`id` (UUID, PK), `email` (unique), `full_name`, `timezone`, `locale`,
`is_active`, `created_at`, `updated_at`. Kept intentionally minimal and
extensible.

### preferences
Separate from `users` to avoid duplicating identity data. `theme`,
`language`, and a flexible JSONB `notifications` map. One row per user.

### devices
Registered devices: `device_id` (unique external id), `platform`,
`device_name`, `last_seen`.

### sessions
Persistent sessions: `session_id` (`id`), `user_id`, `created_at`,
`expires_at`, optional `device_id`.

## API Endpoints

| Method | Path                          | Description                       |
|--------|-------------------------------|-----------------------------------|
| POST   | `/identity/users`             | Create a user (+ default prefs)   |
| GET    | `/identity/users/{id}`        | Fetch a user by id                |
| PATCH  | `/identity/users/{id}`        | Partially update a user           |
| GET    | `/identity/preferences/{user_id}` | Fetch a user's preferences    |
| PATCH  | `/identity/preferences/{user_id}` | Update a user's preferences    |
| GET    | `/identity/devices/{user_id}` | List a user's registered devices  |

Errors: `404` for missing user/preferences, `409` for duplicate email.

## Running

```bash
# Tests (no Postgres needed — uses in-memory SQLite)
pip install -r requirements-dev.txt
pytest

# Full stack with Postgres + migrations
docker compose up --build

# Apply migrations manually
alembic upgrade head
```

## Out of Scope (future sprints)

Memory, Planner, AI Providers, LLM integration, Chat, Voice, Agents, Sync
Engine.
