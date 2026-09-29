"""Deterministic, timezone-aware temporal interpretation for live requests."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.core.time import Clock, InvalidTimezoneError, ensure_utc, resolve_timezone

TemporalKind = Literal["now", "day", "daypart", "week", "freshness"]
TemporalLabel = Literal[
    "now",
    "today",
    "tomorrow",
    "yesterday",
    "this_morning",
    "this_afternoon",
    "this_evening",
    "tonight",
    "last_night",
    "this_week",
    "next_week",
    "recent",
    "latest",
]


@dataclass(frozen=True, slots=True)
class TemporalInterpretation:
    """Canonical local-calendar meaning, independent of any live provider."""

    kind: TemporalKind
    relative_label: TemporalLabel
    timezone: str
    local_date: date
    start_at: datetime | None = None
    end_at: datetime | None = None


_PHRASE_RE = re.compile(
    r"\b(?:this\s+afternoon|this\s+morning|this\s+evening|"
    r"last\s+night|this\s+week|next\s+week|right\s+now|tomorrow|yesterday|"
    r"tonight|today|latest|recent|current|now)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_RE = re.compile(r"(?:what|how)\s+about\s+.+", re.IGNORECASE)
_AMBIGUOUS_RE = re.compile(
    r"\b(?:later|soon|before|after\s+that|recently)\b",
    re.IGNORECASE,
)

_LABELS: dict[str, TemporalLabel] = {
    "now": "now",
    "right now": "now",
    "current": "now",
    "today": "today",
    "tomorrow": "tomorrow",
    "yesterday": "yesterday",
    "this morning": "this_morning",
    "this afternoon": "this_afternoon",
    "this evening": "this_evening",
    "tonight": "tonight",
    "last night": "last_night",
    "this week": "this_week",
    "next week": "next_week",
    "recent": "recent",
    "latest": "latest",
}


def interpret_temporal(
    text: str,
    *,
    clock: Clock,
    timezone_name: str | None,
) -> TemporalInterpretation | None:
    """Interpret one explicit relative phrase using the injected clock.

    Invalid or absent request timezones follow Rocky's existing conservative
    fallback to UTC. Callers should pass the authenticated user's timezone
    when the request does not provide an override.
    """

    match = _PHRASE_RE.search(text)
    if match is None:
        return None
    label = _LABELS[" ".join(match.group(0).lower().split())]
    zone = _timezone(timezone_name)
    now_local = ensure_utc(clock.now()).astimezone(zone)
    today = now_local.date()

    if label == "now":
        return TemporalInterpretation(
            kind="now",
            relative_label=label,
            timezone=zone.key,
            local_date=today,
            start_at=now_local,
        )
    if label in {"recent", "latest"}:
        return TemporalInterpretation(
            kind="freshness",
            relative_label=label,
            timezone=zone.key,
            local_date=today,
            end_at=now_local,
        )
    if label in {"this_week", "next_week"}:
        week_start = today - timedelta(days=today.weekday())
        if label == "next_week":
            week_start += timedelta(days=7)
        start = _local_boundary(week_start, time.min, zone)
        end = _local_boundary(week_start + timedelta(days=7), time.min, zone)
        return TemporalInterpretation(
            kind="week",
            relative_label=label,
            timezone=zone.key,
            local_date=week_start,
            start_at=start,
            end_at=end,
        )
    if label in {"today", "tomorrow", "yesterday"}:
        offset = {"today": 0, "tomorrow": 1, "yesterday": -1}[label]
        target = today + timedelta(days=offset)
        start = _local_boundary(target, time.min, zone)
        end = _local_boundary(target + timedelta(days=1), time.min, zone)
        return TemporalInterpretation(
            kind="day",
            relative_label=label,
            timezone=zone.key,
            local_date=target,
            start_at=start,
            end_at=end,
        )

    target, start_time, end_date, end_time = _daypart_bounds(label, today)
    return TemporalInterpretation(
        kind="daypart",
        relative_label=label,
        timezone=zone.key,
        local_date=target,
        start_at=_local_boundary(target, start_time, zone),
        end_at=_local_boundary(end_date, end_time, zone),
    )


def is_temporal_follow_up(
    message: str, temporal: TemporalInterpretation | None
) -> bool:
    """Return whether the whole message is a bounded temporal follow-up."""

    if temporal is None:
        return False
    return bool(_FOLLOW_UP_RE.fullmatch(message.strip(" ?.\t\n")))


def strip_trailing_temporal_phrase(value: str) -> str:
    """Remove one recognized temporal suffix from a parsed location slot."""

    match = _PHRASE_RE.search(value)
    if match is None or value[match.end() :].strip(" ?.\t\n"):
        return value
    return value[: match.start()].rstrip(" ,")


def has_ambiguous_temporal_language(text: str) -> bool:
    """Recognize deliberately unsupported relative-time wording."""

    return bool(_AMBIGUOUS_RE.search(text))


def _timezone(timezone_name: str | None):
    try:
        return resolve_timezone(timezone_name or "UTC")
    except InvalidTimezoneError:
        return resolve_timezone("UTC")


def _local_boundary(local_date: date, local_time: time, zone) -> datetime:
    return datetime.combine(local_date, local_time, tzinfo=zone)


def _daypart_bounds(
    label: TemporalLabel, today: date
) -> tuple[date, time, date, time]:
    if label == "this_morning":
        return today, time(5), today, time(12)
    if label == "this_afternoon":
        return today, time(12), today, time(17)
    if label == "this_evening":
        return today, time(17), today, time(21)
    if label == "tonight":
        return today, time(21), today + timedelta(days=1), time(5)
    if label == "last_night":
        previous = today - timedelta(days=1)
        return previous, time(21), today, time(5)
    raise ValueError(f"Unsupported daypart: {label}")
