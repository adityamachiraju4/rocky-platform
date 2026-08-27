"""TheSportsDB sports lookup provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveProviderUnavailable
from app.live.schemas import SourceMetadata, SportsArgs, SportsEvent, SportsReport


class TheSportsDbProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def lookup(self, args: SportsArgs) -> SportsReport:
        retrieved_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"https://www.thesportsdb.com/api/v1/json/{self._api_key}/searchevents.php",
                    params={"e": args.query},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Sports provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Sports provider failed.") from exc
        return _normalize(args, response.json(), retrieved_at)


def _normalize(
    args: SportsArgs, payload: dict[str, Any], retrieved_at: datetime
) -> SportsReport:
    raw_events = payload.get("event")
    if not isinstance(raw_events, list):
        raise LiveMalformedResult("Sports provider returned no usable events.")
    events: list[SportsEvent] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        name = item.get("strEvent")
        if not isinstance(name, str) or not name.strip():
            continue
        score = _score(item)
        events.append(
            SportsEvent(
                name=name.strip(),
                status=item.get("strStatus") if isinstance(item.get("strStatus"), str) else None,
                start_time=_event_time(item),
                score=score,
                league=item.get("strLeague") if isinstance(item.get("strLeague"), str) else None,
                url=(
                    f"https://www.thesportsdb.com/event/{item['idEvent']}"
                    if isinstance(item.get("idEvent"), str)
                    else None
                ),
            )
        )
        if len(events) >= args.max_results:
            break
    if not events:
        raise LiveMalformedResult("Sports provider returned no usable events.")
    return SportsReport(
        query=args.query,
        events=tuple(events),
        source=SourceMetadata(
            provider="thesportsdb",
            retrieved_at=retrieved_at,
            title="TheSportsDB",
            url="https://www.thesportsdb.com/",
            freshness="provider event lookup at request time",
        ),
    )


def _score(item: dict[str, Any]) -> str | None:
    home = item.get("intHomeScore")
    away = item.get("intAwayScore")
    if isinstance(home, str) and isinstance(away, str) and home and away:
        return f"{home}-{away}"
    return None


def _event_time(item: dict[str, Any]) -> datetime | None:
    date = item.get("dateEvent")
    time_value = item.get("strTime")
    if not isinstance(date, str):
        return None
    try:
        if isinstance(time_value, str) and time_value:
            return datetime.fromisoformat(f"{date}T{time_value.replace('Z', '+00:00')}")
        return datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
