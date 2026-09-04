"""Open-Meteo weather provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.live.errors import (
    LiveMalformedResult,
    LiveProviderTimeout,
    LiveProviderUnavailable,
    LiveUnsupportedRequest,
)
from app.live.schemas import SourceMetadata, WeatherArgs, WeatherReport

_WEATHER_CODES = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
}


class OpenMeteoWeatherProvider:
    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def weather(self, args: WeatherArgs) -> WeatherReport:
        retrieved_at = datetime.now(timezone.utc)
        if args.latitude is not None and args.longitude is not None:
            latitude, longitude, resolved_name = (
                args.latitude,
                args.longitude,
                args.location,
            )
        else:
            latitude, longitude, resolved_name = await self._geocode(args.location)
        data = await self._forecast(latitude, longitude)
        return _normalize_weather(args, data, resolved_name, retrieved_at)

    async def _geocode(self, location: str) -> tuple[float, float, str]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": location, "count": 1, "language": "en", "format": "json"},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Weather location lookup timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Weather location lookup failed.") from exc
        payload = response.json()
        results = payload.get("results")
        if not results:
            raise LiveUnsupportedRequest(
                "I couldn't resolve that location for weather."
            )
        first = results[0]
        try:
            name_parts = [
                str(value)
                for value in (
                    first.get("name"),
                    first.get("admin1"),
                    first.get("country"),
                )
                if value
            ]
            return (
                float(first["latitude"]),
                float(first["longitude"]),
                ", ".join(dict.fromkeys(name_parts)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveMalformedResult("Weather geocoding result was malformed.") from exc

    async def _forecast(self, latitude: float, longitude: float) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
                        "forecast_days": 3,
                        "timezone": "auto",
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Weather provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Weather provider failed.") from exc
        return response.json()


def _normalize_weather(
    args: WeatherArgs,
    payload: dict[str, Any],
    resolved_location: str,
    retrieved_at: datetime,
) -> WeatherReport:
    source = SourceMetadata(
        provider="open-meteo",
        retrieved_at=retrieved_at,
        title="Open-Meteo Forecast API",
        url="https://open-meteo.com/",
        freshness="forecast API response at request time",
    )
    if args.window == "current":
        current = payload.get("current")
        if not isinstance(current, dict):
            raise LiveMalformedResult("Weather current result was malformed.")
        return WeatherReport(
            location=resolved_location,
            window=args.window,
            temperature_c=_float_or_none(current.get("temperature_2m")),
            condition=_condition(current.get("weather_code")),
            precipitation_mm=_float_or_none(current.get("precipitation")),
            wind_kph=_float_or_none(current.get("wind_speed_10m")),
            source=source,
        )

    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise LiveMalformedResult("Weather forecast result was malformed.")
    index = 1 if args.window == "tomorrow" else 0
    return WeatherReport(
        location=resolved_location,
        window=args.window,
        temperature_c=_average_temperature(daily, index),
        condition=_condition(_daily_value(daily, "weather_code", index)),
        precipitation_probability=_int_or_none(
            _daily_value(daily, "precipitation_probability_max", index)
        ),
        precipitation_mm=_float_or_none(_daily_value(daily, "precipitation_sum", index)),
        forecast_date=str(_daily_value(daily, "time", index) or ""),
        source=source,
    )


def _daily_value(daily: dict[str, Any], key: str, index: int) -> object:
    values = daily.get(key)
    if not isinstance(values, list) or len(values) <= index:
        raise LiveMalformedResult("Weather forecast field was malformed.")
    return values[index]


def _average_temperature(daily: dict[str, Any], index: int) -> float | None:
    high = _float_or_none(_daily_value(daily, "temperature_2m_max", index))
    low = _float_or_none(_daily_value(daily, "temperature_2m_min", index))
    if high is None or low is None:
        return None
    return round((high + low) / 2, 1)


def _condition(value: object) -> str | None:
    code = _int_or_none(value)
    return _WEATHER_CODES.get(code, f"weather code {code}") if code is not None else None


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (float, int)) else None


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, (float, int)) else None
