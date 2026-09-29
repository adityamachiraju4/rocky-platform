"""Canonical temporal interpretation for public live-information routing."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.core.time import FrozenClock
from app.live.temporal import interpret_temporal


FIXED_CLOCK = FrozenClock(datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    ("phrase", "kind", "label", "local_date", "start_hour", "end_hour"),
    [
        ("now", "now", "now", date(2026, 9, 28), 17, None),
        ("today", "day", "today", date(2026, 9, 28), 0, 0),
        ("tomorrow", "day", "tomorrow", date(2026, 9, 29), 0, 0),
        ("yesterday", "day", "yesterday", date(2026, 9, 27), 0, 0),
        ("this morning", "daypart", "this_morning", date(2026, 9, 28), 5, 12),
        ("this afternoon", "daypart", "this_afternoon", date(2026, 9, 28), 12, 17),
        ("this evening", "daypart", "this_evening", date(2026, 9, 28), 17, 21),
        ("tonight", "daypart", "tonight", date(2026, 9, 28), 21, 5),
        ("last night", "daypart", "last_night", date(2026, 9, 27), 21, 5),
        ("this week", "week", "this_week", date(2026, 9, 28), 0, 0),
        ("next week", "week", "next_week", date(2026, 10, 5), 0, 0),
    ],
)
def test_temporal_phrases_use_fixed_clock_and_kolkata_calendar(
    phrase: str,
    kind: str,
    label: str,
    local_date: date,
    start_hour: int,
    end_hour: int | None,
) -> None:
    result = interpret_temporal(
        phrase,
        clock=FIXED_CLOCK,
        timezone_name="Asia/Kolkata",
    )

    assert result is not None
    assert result.kind == kind
    assert result.relative_label == label
    assert result.local_date == local_date
    assert result.timezone == "Asia/Kolkata"
    assert result.start_at is not None
    assert result.start_at.hour == start_hour
    if end_hour is None:
        assert result.end_at is None
    else:
        assert result.end_at is not None
        assert result.end_at.hour == end_hour


def test_today_uses_each_effective_timezones_local_date() -> None:
    clock = FrozenClock(datetime(2026, 9, 28, 23, 30, tzinfo=timezone.utc))

    kolkata = interpret_temporal(
        "today", clock=clock, timezone_name="Asia/Kolkata"
    )
    los_angeles = interpret_temporal(
        "today", clock=clock, timezone_name="America/Los_Angeles"
    )

    assert kolkata is not None and kolkata.local_date == date(2026, 9, 29)
    assert los_angeles is not None and los_angeles.local_date == date(2026, 9, 28)


def test_night_intervals_cross_local_midnight_explicitly() -> None:
    tonight = interpret_temporal(
        "tonight", clock=FIXED_CLOCK, timezone_name="Asia/Kolkata"
    )
    last_night = interpret_temporal(
        "last night", clock=FIXED_CLOCK, timezone_name="Asia/Kolkata"
    )

    assert tonight is not None and tonight.start_at is not None
    assert tonight.end_at is not None
    assert tonight.start_at.date() == date(2026, 9, 28)
    assert tonight.end_at.date() == date(2026, 9, 29)
    assert last_night is not None and last_night.start_at is not None
    assert last_night.end_at is not None
    assert last_night.start_at.date() == date(2026, 9, 27)
    assert last_night.end_at.date() == date(2026, 9, 28)


def test_dst_day_uses_local_calendar_boundaries_not_24_elapsed_hours() -> None:
    clock = FrozenClock(datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc))

    result = interpret_temporal(
        "today", clock=clock, timezone_name="America/New_York"
    )

    assert result is not None
    assert result.local_date == date(2026, 3, 8)
    assert result.start_at is not None and result.end_at is not None
    assert result.start_at.astimezone(timezone.utc) == datetime(
        2026, 3, 8, 5, 0, tzinfo=timezone.utc
    )
    assert result.end_at.astimezone(timezone.utc) == datetime(
        2026, 3, 9, 4, 0, tzinfo=timezone.utc
    )
    assert (
        result.end_at.astimezone(timezone.utc)
        - result.start_at.astimezone(timezone.utc)
    ).total_seconds() == 23 * 60 * 60


@pytest.mark.parametrize("phrase", ["later", "soon", "before", "after that", "recently"])
def test_ambiguous_temporal_language_is_not_over_interpreted(phrase: str) -> None:
    assert (
        interpret_temporal(
            phrase,
            clock=FIXED_CLOCK,
            timezone_name="Asia/Kolkata",
        )
        is None
    )
