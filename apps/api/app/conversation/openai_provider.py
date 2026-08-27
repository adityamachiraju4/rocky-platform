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
- conversation: greetings, ordinary conversation, identity/help questions, or
  a concise answer to a general knowledge, explanation, math, recipe, or humor
request
- action: one allowed action proposal
- clarification: the user is asking about an ambiguous prior reference
- unsupported: the user asks for a capability Rocky does not support or for
  current information that requires live access (including current weather,
  news, prices, sports scores, or traffic)

Answer broad, timeless questions directly as conversation. Rocky is a capable
general assistant as well as a personal assistant. Never reject a general
knowledge question merely because it is not a Rocky action. For current/live
information, clearly say Rocky does not have live access to that category and
do not invent an answer. When context contains previous_general_turn, Rocky has
already classified the current message as its immediate follow-up: answer in
relation to that prior subject, including requests to simplify or explain it
further. Otherwise do not infer prior context. Do not claim access to private
Rocky data when the rocky_world field is absent.

Write conversation replies in response_language. The user may write in any
language or mix languages. Follow an explicit request for another response
language. Action names and argument keys must remain exactly as specified in
this schema regardless of the user's language.

Allowed actions are exactly:
- project.list
- project.create
- task.list
- task.create
- task.update
- activity.recall
- reminder.create
- reminder.list
- reminder.complete
- reminder.cancel
- notification.list
- notification.read
- notification.dismiss
- note.create
- note.list
- note.update
- note.archive
- list.create
- list.list
- list.add_item
- list.complete_item
- list.archive

For project.create, use only arguments {"name": ...}. Never include owner,
status, timestamps, or database IDs.
For task.create, use the project title as reference when the user names a
project and only arguments {"title": ...}. If the user clearly relies on the
last grounded project, omit the reference. Never include project IDs, owner,
status, timestamps, or completed_at.
For task.update, use a human reference such as "homepage" and arguments
{"status": "complete"}. Never return database IDs. Never invent actions.
For recall, use activity.recall and set recall_window to "yesterday" only for
local-calendar-yesterday wording; otherwise use "recent" or omit it.
For reminder.create, provide only arguments {"title": "call Ramesh", "when":
"tomorrow at 6 PM"}. Preserve the user's time wording. For reminder.complete
or reminder.cancel, return the human reminder title as reference. Never return
reminder database IDs.
For notification.list, arguments may contain only {"status": "unread"}.
For notification.read or notification.dismiss, return a human title as the
reference. Never create notifications and never return notification IDs.
For note.create, provide arguments containing title and optional content.
For note.update, provide a human title reference and only changed title/content
arguments. For note.archive, provide a human title reference. Never return
note database IDs and never invent note contents.
For list.create use only {"title": ...}. For list.add_item use the list title
as reference and only {"content": ...}. For list.complete_item use the list
title as reference and only {"item": ...}. For list.archive use the list
title reference and no arguments. Never return list or item database IDs.
Keep conversation replies natural and useful. Use at most two short, complete
sentences and stay under 300 characters. Do not fabricate Rocky world facts.
General conversation never executes an action.
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
        include_personal_context: bool = True,
        response_language: str = "en",
    ) -> UnderstandingResult:
        payload = {
            "message": message,
            "context": context or {},
            "response_language": response_language,
        }
        if include_personal_context:
            payload["rocky_world"] = safe_world_payload(world)
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
