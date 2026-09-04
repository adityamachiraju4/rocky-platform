"""Registration orchestration across identity and verification delivery."""
from __future__ import annotations

from app.auth.action_service import AuthActionService
from app.models.user import User

from .schemas import UserCreate
from .service import IdentityService


class RegistrationService:
    def __init__(self, identity: IdentityService, auth_actions: AuthActionService) -> None:
        self._identity = identity
        self._auth_actions = auth_actions

    async def register(self, data: UserCreate) -> User:
        user = await self._identity.create_user(data)
        await self._auth_actions.send_verification_for_user(user, enforce_cooldown=False)
        return user
