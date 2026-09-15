from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.db.session import get_database_url


def test_get_database_url_preserves_database_url_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "test-password-123"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://postgres:{password}@db.example.test:5432/rocky",
    )
    monkeypatch.setenv("POSTGRES_USER", "ignored-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ignored-password")
    monkeypatch.setenv("POSTGRES_DB", "ignored-database")

    rendered_url = get_database_url()

    assert make_url(rendered_url).password == password
    assert "***" not in rendered_url
    assert rendered_url.startswith("postgresql+asyncpg://")
