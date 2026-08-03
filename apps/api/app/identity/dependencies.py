"""Dependency-injection wiring for the Identity subsystem.

The providers are defined in :mod:`app.core.dependencies` (the centralized
composition layer, Platform-005) and re-exported here so existing imports
(e.g. ``from .dependencies import IdentityServiceDep``) keep working and
resolve to the same objects.
"""
from __future__ import annotations

from app.core.dependencies import get_identity_service, IdentityServiceDep

__all__ = ["get_identity_service", "IdentityServiceDep"]
