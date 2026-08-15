"""Authentication capability package for Project Rocky.

Owns the authentication *lifecycle*: login, refresh-token rotation, logout,
and the ``get_current_user`` dependency. Composes the credential-verification
``AuthService`` with the session/refresh-token ``SessionService`` and the
canonical Identity models. Dependency direction is Capability -> Platform.
"""
from __future__ import annotations
