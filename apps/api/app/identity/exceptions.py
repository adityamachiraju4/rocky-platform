"""Domain exceptions for the Identity subsystem.

These are pure domain errors. They contain no HTTP semantics — the router
layer is responsible for translating them into HTTP responses.
"""
from __future__ import annotations


class IdentityError(Exception):
    """Base class for all identity domain errors."""


class UserNotFoundError(IdentityError):
    """Raised when a user cannot be located."""


class EmailAlreadyExistsError(IdentityError):
    """Raised when creating a user with an email that already exists."""


class PreferencesNotFoundError(IdentityError):
    """Raised when preferences for a user cannot be located."""
