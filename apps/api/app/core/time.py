"""Timezone-safe clock and conversion primitives for Rocky."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class TimeError(ValueError):
    """Base class for invalid time input."""


class InvalidTimezoneError(TimeError):
    """Raised when an IANA timezone name is unknown."""


class NaiveDateTimeError(TimeError):
    """Raised when an operation requires an aware datetime."""


class AmbiguousLocalTimeError(TimeError):
    """Raised when a local wall time occurs twice during a DST transition."""


class NonexistentLocalTimeError(TimeError):
    """Raised when a local wall time is skipped during a DST transition."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    current: datetime

    def now(self) -> datetime:
        return ensure_utc(self.current)


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"Unknown IANA timezone: {name!r}") from exc


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDateTimeError("An aware datetime is required.")
    return value.astimezone(UTC)


def local_datetime_to_utc(
    value: datetime,
    timezone_name: str,
    *,
    fold: int | None = None,
) -> datetime:
    """Resolve a naive local wall time to UTC without guessing at DST edges."""
    if value.tzinfo is not None:
        raise TimeError("Local wall time must be naive.")
    if fold not in (None, 0, 1):
        raise TimeError("fold must be 0, 1, or None.")

    zone = resolve_timezone(timezone_name)

    def candidate(candidate_fold: int) -> datetime | None:
        aware = value.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = aware.astimezone(UTC).astimezone(zone)
        if (
            round_trip.replace(tzinfo=None) != value
            or round_trip.fold != candidate_fold
        ):
            return None
        return aware.astimezone(UTC)

    first = candidate(0)
    second = candidate(1)
    if first is None and second is None:
        raise NonexistentLocalTimeError(
            f"{value.isoformat()} does not exist in {timezone_name}."
        )
    if first is not None and second is not None and first != second:
        if fold is None:
            raise AmbiguousLocalTimeError(
                f"{value.isoformat()} occurs twice in {timezone_name}."
            )
        return first if fold == 0 else second
    return first if first is not None else second  # type: ignore[return-value]


def utc_to_local(value: datetime, timezone_name: str) -> datetime:
    return ensure_utc(value).astimezone(resolve_timezone(timezone_name))


system_clock = SystemClock()

