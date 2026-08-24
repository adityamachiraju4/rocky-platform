"""Deterministic parsing of the small reminder language supported by v1."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.core.time import Clock, local_datetime_to_utc, resolve_timezone


@dataclass(frozen=True, slots=True)
class ReminderIntent:
    title: str
    when: str


_TIME = r"(?:today\s+|tomorrow\s+)?(?:at\s+)?(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)?"
_CREATE_PATTERNS = (
    re.compile(
        rf"^remind\s+me\s+(?P<when>{_TIME})\s+to\s+(?P<title>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^remind\s+me\s+to\s+(?P<title>.+?)\s+(?P<when>{_TIME})$",
        re.IGNORECASE,
    ),
)


def extract_reminder_intent(message: str) -> ReminderIntent | None:
    text = " ".join(message.strip().split())
    for pattern in _CREATE_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            title = match.group("title").strip(" .")
            when = match.group("when").strip(" .")
            if title and when:
                return ReminderIntent(title=title, when=when)
    return None


def interpret_reminder_time(
    when: str, timezone_name: str, clock: Clock
) -> datetime:
    """Interpret a supported wall-clock phrase in the user's timezone."""
    zone = resolve_timezone(timezone_name)
    now_local = clock.now().astimezone(zone)
    normalized = when.lower().replace(".", "").strip()
    day_offset: int | None = None
    if normalized.startswith("tomorrow "):
        day_offset = 1
        normalized = normalized.removeprefix("tomorrow ")
    elif normalized.startswith("today "):
        day_offset = 0
        normalized = normalized.removeprefix("today ")
    normalized = normalized.removeprefix("at ").strip()

    match = re.fullmatch(
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)?",
        normalized,
    )
    if match is None:
        raise ValueError("I couldn't understand that reminder time.")

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    period = match.group("period")
    if period:
        if not 1 <= hour <= 12:
            raise ValueError("A 12-hour time must use an hour from 1 to 12.")
        hour = hour % 12 + (12 if period == "pm" else 0)
    elif hour > 23:
        raise ValueError("A 24-hour time must use an hour from 0 to 23.")

    local_date = now_local.date() + timedelta(days=day_offset or 0)
    local_value = datetime.combine(local_date, time(hour, minute))
    due_at = local_datetime_to_utc(local_value, timezone_name)
    if day_offset is None and due_at <= clock.now():
        due_at = local_datetime_to_utc(
            local_value + timedelta(days=1), timezone_name
        )
    if day_offset == 0 and due_at <= clock.now():
        raise ValueError("That time has already passed today.")
    return due_at
