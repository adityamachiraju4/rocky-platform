"""OpenAI-backed Natural Understanding provider for Conversation.

This module is an adapter only. It uses the Responses API to produce a
schema-constrained Rocky-owned interpretation; it never executes actions.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.conversation.resolver import WorldView
from app.conversation.understanding import (
    UNDERSTANDING_JSON_SCHEMA,
    UnderstandingProviderError,
    UnderstandingResult,
    parse_understanding_payload,
    safe_world_payload,
)

logger = logging.getLogger(__name__)


_SYSTEM_INSTRUCTIONS = """You are Rocky's Natural Understanding layer.
Return only the requested JSON shape. You do not execute actions.

Classify the user's utterance as one of:
- conversation: short greetings, thanks, small talk, or a task status question
- action: one allowed action proposal
- clarification: the user is asking about an ambiguous prior reference
- unsupported: the user asks for a capability Rocky does not support

Allowed actions are exactly:
- project.list
- task.list
- task.update
- activity.recall

For task.update, use a human reference such as "homepage" and arguments
{"status": "complete"}. Never return database IDs. Never invent actions.
For recall, use activity.recall and set recall_window to "yesterday" only for
local-calendar-yesterday wording; otherwise use "recent" or omit it.
Keep conversation replies short and do not fabricate Rocky world facts.
"""


class OpenAIUnderstandingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment guard
            raise UnderstandingProviderError(
                "OpenAI SDK is not installed."
            ) from exc

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self.last_error_code: str | None = None

    async def understand(
        self,
        *,
        message: str,
        world: WorldView,
        context: dict[str, Any] | None = None,
    ) -> UnderstandingResult:
        payload = {
            "message": message,
            "world": safe_world_payload(world),
            "context": context or {},
        }
        self.last_error_code = None

        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": _SYSTEM_INSTRUCTIONS,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": json.dumps(payload),
                            }
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "rocky_understanding",
                        "schema": UNDERSTANDING_JSON_SCHEMA,
                        "strict": True,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 - provider failure degrades
            self.last_error_code = "request_failed"
            logger.warning(
                "OpenAI understanding request failed",
                extra={"provider_error_code": self.last_error_code},
            )
            raise UnderstandingProviderError(
                "OpenAI understanding request failed.",
                code=self.last_error_code,
            ) from exc

        try:
            raw = response.output_text
        except AttributeError as exc:
            self.last_error_code = "missing_output_text"
            logger.warning(
                "OpenAI understanding response missing output text",
                extra={"provider_error_code": self.last_error_code},
            )
            raise UnderstandingProviderError(
                "OpenAI response did not include output_text.",
                code=self.last_error_code,
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_error_code = "invalid_json"
            logger.warning(
                "OpenAI understanding response was not JSON",
                extra={"provider_error_code": self.last_error_code},
            )
            raise UnderstandingProviderError(
                "OpenAI response was not valid JSON.",
                code=self.last_error_code,
            ) from exc

        try:
            return parse_understanding_payload(data)
        except UnderstandingProviderError as exc:
            self.last_error_code = exc.code
            logger.warning(
                "OpenAI understanding response failed validation",
                extra={"provider_error_code": self.last_error_code},
            )
            raise
