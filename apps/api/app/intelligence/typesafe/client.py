"""Small async client for TypeSafe's documented v1 API."""
from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.intelligence.decision import DecisionProviderError
from app.intelligence.typesafe.models import ModelsResponse, SystemOneRequest, SystemOneResponse

ModelT = TypeVar("ModelT", bound=BaseModel)


class TypeSafeError(DecisionProviderError):
    code = "typesafe_error"


class TypeSafeUnavailable(TypeSafeError):
    code = "typesafe_unavailable"


class TypeSafeTimeout(TypeSafeUnavailable):
    code = "typesafe_timeout"


class TypeSafeAuthenticationError(TypeSafeError):
    code = "typesafe_authentication"


class TypeSafeRateLimitError(TypeSafeUnavailable):
    code = "typesafe_rate_limit"


class TypeSafeInvalidResponse(TypeSafeError):
    code = "typesafe_invalid_response"


class TypeSafeModelUnavailable(TypeSafeError):
    code = "typesafe_model_unavailable"


class TypeSafeClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise TypeSafeAuthenticationError("TypeSafe API key is missing")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client

    async def list_models(self) -> ModelsResponse:
        response = await self._request("GET", "/v1/models")
        return self._parse(response, ModelsResponse)

    async def require_model(self, model: str) -> None:
        available = await self.list_models()
        if model not in {item.name for item in available.models}:
            raise TypeSafeModelUnavailable("configured TypeSafe model is unavailable")

    async def system_one(self, request: SystemOneRequest) -> SystemOneResponse:
        response = await self._request(
            "POST",
            "/v1/systemone",
            json=request.model_dump(mode="json"),
            detect_model_rejection=True,
        )
        return self._parse(response, SystemOneResponse)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        detect_model_rejection: bool = False,
    ) -> httpx.Response:
        try:
            if self._client is not None:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=self._headers,
                    json=json,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method,
                        f"{self._base_url}{path}",
                        headers=self._headers,
                        json=json,
                    )
        except httpx.TimeoutException as exc:
            raise TypeSafeTimeout("TypeSafe request timed out") from exc
        except httpx.HTTPError as exc:
            raise TypeSafeUnavailable("TypeSafe request failed") from exc

        if response.status_code in {401, 403}:
            raise TypeSafeAuthenticationError("TypeSafe authentication failed")
        if response.status_code == 429:
            raise TypeSafeRateLimitError("TypeSafe rate limit reached")
        if response.status_code >= 500:
            raise TypeSafeUnavailable("TypeSafe service is unavailable")
        if response.status_code >= 400:
            if detect_model_rejection and self._rejects_model(response):
                raise TypeSafeModelUnavailable(
                    "configured TypeSafe model was rejected"
                )
            raise TypeSafeError(f"TypeSafe request failed with status {response.status_code}")
        return response

    @staticmethod
    def _rejects_model(response: httpx.Response) -> bool:
        """Recognize model-specific validation without exposing response data."""

        if response.status_code == 404:
            return True
        try:
            payload = response.json()
        except ValueError:
            return False

        def mentions_model(value: object) -> bool:
            if isinstance(value, str):
                return "model" in value.lower()
            if isinstance(value, list):
                return any(mentions_model(item) for item in value)
            if isinstance(value, dict):
                return any(
                    "model" in str(key).lower() or mentions_model(item)
                    for key, item in value.items()
                )
            return False

        return mentions_model(payload)

    @staticmethod
    def _parse(response: httpx.Response, model_type: type[ModelT]) -> ModelT:
        try:
            return model_type.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise TypeSafeInvalidResponse("TypeSafe returned an invalid response") from exc


__all__ = [
    "TypeSafeAuthenticationError",
    "TypeSafeClient",
    "TypeSafeError",
    "TypeSafeInvalidResponse",
    "TypeSafeModelUnavailable",
    "TypeSafeRateLimitError",
    "TypeSafeTimeout",
    "TypeSafeUnavailable",
]
