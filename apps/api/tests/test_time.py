from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.time import (
    AmbiguousLocalTimeError,
    InvalidTimezoneError,
    NaiveDateTimeError,
    NonexistentLocalTimeError,
    ensure_utc,
    local_datetime_to_utc,
    normalize_timezone,
    resolve_timezone,
    utc_to_local,
)


def test_aware_datetime_normalizes_to_utc() -> None:
    local = datetime(2026, 8, 24, 18, 0, tzinfo=resolve_timezone("Asia/Kolkata"))
    assert ensure_utc(local) == datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)


def test_naive_datetime_is_rejected_for_utc_storage() -> None:
    with pytest.raises(NaiveDateTimeError):
        ensure_utc(datetime(2026, 8, 24, 18, 0))


def test_local_wall_time_round_trip() -> None:
    utc_value = local_datetime_to_utc(
        datetime(2026, 8, 24, 18, 0), "Asia/Kolkata"
    )
    assert utc_value == datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)
    assert utc_to_local(utc_value, "Asia/Kolkata").hour == 18


def test_nonexistent_dst_time_is_rejected() -> None:
    with pytest.raises(NonexistentLocalTimeError):
        local_datetime_to_utc(
            datetime(2026, 3, 8, 2, 30), "America/New_York"
        )


def test_ambiguous_dst_time_requires_explicit_fold() -> None:
    local = datetime(2026, 11, 1, 1, 30)
    with pytest.raises(AmbiguousLocalTimeError):
        local_datetime_to_utc(local, "America/New_York")

    first = local_datetime_to_utc(local, "America/New_York", fold=0)
    second = local_datetime_to_utc(local, "America/New_York", fold=1)
    assert second > first


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(InvalidTimezoneError):
        resolve_timezone("Mars/Olympus_Mons")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Asia/Calcutta", "Asia/Kolkata"),
        (" Asia/Kolkata ", "Asia/Kolkata"),
        ("America/New_York", "America/New_York"),
        ("US/Eastern", "America/New_York"),
        ("UTC", "UTC"),
    ],
)
def test_timezone_names_are_normalized(value: str, expected: str) -> None:
    assert normalize_timezone(value) == expected
    assert resolve_timezone(value).key == expected


def test_invalid_timezone_name_is_rejected_by_normalizer() -> None:
    with pytest.raises(InvalidTimezoneError):
        normalize_timezone("Mars/Olympus")
