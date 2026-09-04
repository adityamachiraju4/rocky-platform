"""Safe, bounded diagnostics for speech-provider failures."""
from __future__ import annotations

import re
from typing import Any

_MAX_REASON_LENGTH = 240
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
    re.compile(
        r"(?i)\b(api[_ -]?key|authorization|bearer|token)\b\s*[:=]?\s*[^\s,;]+"
    ),
)


def safe_reason(value: object) -> str:
    rendered = " ".join(str(value).split())
    for pattern in _SECRET_PATTERNS:
        rendered = pattern.sub("[redacted]", rendered)
    return (rendered or "No provider reason supplied.")[:_MAX_REASON_LENGTH]


def exception_details(exc: Exception) -> tuple[str, str]:
    return exc.__class__.__name__, safe_reason(exc)


def openai_exception_details(exc: Exception) -> dict[str, object]:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error: Any = body["error"]
    else:
        error = body

    error_type = getattr(exc, "type", None) or exc.__class__.__name__
    error_code = getattr(exc, "code", None)
    reason: object = getattr(exc, "message", None) or str(exc)
    if isinstance(error, dict):
        error_type = error.get("type") or error_type
        error_code = error.get("code") or error_code
        reason = error.get("message") or reason
    return {
        "status": status,
        "error_type": str(error_type),
        "error_code": error_code,
        "reason": safe_reason(reason),
    }
