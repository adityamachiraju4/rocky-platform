"""Local timezone-backed live time provider."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.live.errors import LiveUnsupportedRequest
from app.live.schemas import SourceMetadata, TimeArgs, TimeReport

_LOCATION_TIMEZONES = {
    "tokyo": "Asia/Tokyo",
    "new york": "America/New_York",
    "london": "Europe/London",
    "chennai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "san francisco": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "paris": "Europe/Paris",
    "sydney": "Australia/Sydney",
}


class LocalTimeProvider:
    async def lookup_time(self, args: TimeArgs) -> TimeReport:
        timezone_name = _resolve_timezone(args.location)
        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        local_time = now_utc.astimezone(ZoneInfo(timezone_name))
        return TimeReport(
            location=args.location,
            timezone=timezone_name,
            local_time=local_time,
            source=SourceMetadata(
                provider="python-zoneinfo",
                retrieved_at=now_utc,
                freshness="computed at request time",
            ),
        )


def _resolve_timezone(value: str) -> str:
    cleaned = value.strip()
    try:
        ZoneInfo(cleaned)
        return cleaned
    except ZoneInfoNotFoundError:
        pass
    mapped = _LOCATION_TIMEZONES.get(cleaned.lower())
    if mapped is None:
        raise LiveUnsupportedRequest(
            "I need a recognizable city or IANA timezone for that time lookup."
        )
    return mapped
