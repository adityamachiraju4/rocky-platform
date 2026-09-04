"""Geoapify places provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveProviderUnavailable
from app.live.schemas import PlaceResult, PlacesArgs, PlacesReport, SourceMetadata

_CATEGORY_BY_QUERY = {
    "restaurant": "catering.restaurant",
    "restaurants": "catering.restaurant",
    "coffee": "catering.cafe",
    "cafe": "catering.cafe",
    "hospital": "healthcare.hospital",
    "hospitals": "healthcare.hospital",
}


class GeoapifyPlacesProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def search_places(self, args: PlacesArgs) -> PlacesReport:
        retrieved_at = datetime.now(timezone.utc)
        category = _category_for(args.query)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if args.latitude is not None and args.longitude is not None:
                    lat, lon = args.latitude, args.longitude
                else:
                    geocoded = await client.get(
                        "https://api.geoapify.com/v1/geocode/search",
                        params={"text": args.location, "limit": 1, "apiKey": self._api_key},
                    )
                    geocoded.raise_for_status()
                    lat, lon = _coordinates(geocoded.json())
                response = await client.get(
                    "https://api.geoapify.com/v2/places",
                    params={
                        "categories": category,
                        "filter": f"circle:{lon},{lat},{args.radius_meters}",
                        "bias": f"proximity:{lon},{lat}",
                        "limit": args.max_results,
                        "apiKey": self._api_key,
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Places provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Places provider failed.") from exc
        return _normalize(args, response.json(), retrieved_at)


def _category_for(query: str) -> str:
    lowered = query.strip().lower()
    return next(
        (category for cue, category in _CATEGORY_BY_QUERY.items() if cue in lowered),
        "commercial",
    )


def _coordinates(payload: dict[str, Any]) -> tuple[float, float]:
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise LiveMalformedResult("Places location result was malformed.")
    geometry = features[0].get("geometry") if isinstance(features[0], dict) else None
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or not isinstance(coordinates[0], (float, int))
        or not isinstance(coordinates[1], (float, int))
    ):
        raise LiveMalformedResult("Places coordinates were malformed.")
    return float(coordinates[1]), float(coordinates[0])


def _normalize(
    args: PlacesArgs, payload: dict[str, Any], retrieved_at: datetime
) -> PlacesReport:
    features = payload.get("features")
    if not isinstance(features, list):
        raise LiveMalformedResult("Places result was malformed.")
    places: list[PlaceResult] = []
    for item in features:
        props = item.get("properties") if isinstance(item, dict) else None
        if not isinstance(props, dict):
            continue
        name = props.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        places.append(
            PlaceResult(
                name=name.strip(),
                address=props.get("formatted") if isinstance(props.get("formatted"), str) else None,
                distance_meters=(
                    int(props["distance"]) if isinstance(props.get("distance"), (float, int)) else None
                ),
            )
        )
        if len(places) >= args.max_results:
            break
    if not places:
        raise LiveMalformedResult("Places provider returned no usable places.")
    return PlacesReport(
        query=args.query,
        location=args.location,
        places=tuple(places),
        source=SourceMetadata(
            provider="geoapify",
            retrieved_at=retrieved_at,
            title="Geoapify Places",
            url="https://www.geoapify.com/places-api/",
            freshness="places response at request time",
        ),
    )
