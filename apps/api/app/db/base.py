"""SQLAlchemy declarative base for all Project Rocky ORM models.

Every ORM model in Rocky inherits from :class:`Base`. Keeping the base in
its own module avoids circular imports between models and the engine/session
configuration in ``session.py``.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in Rocky."""
