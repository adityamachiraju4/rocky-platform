"""Closed registry of read-only live-information tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Type

from app.live.schemas import (
    LiveToolArgs,
    MarketArgs,
    NewsArgs,
    PlacesArgs,
    SportsArgs,
    TimeArgs,
    WeatherArgs,
    WebSearchArgs,
)

WEATHER_CURRENT: Final = "weather.current"
WEATHER_FORECAST: Final = "weather.forecast"
NEWS_SEARCH: Final = "news.search"
WEB_SEARCH: Final = "web.search"
MARKET_QUOTE: Final = "market.quote"
CRYPTO_QUOTE: Final = "crypto.quote"
SPORTS_LOOKUP: Final = "sports.lookup"
PLACES_SEARCH: Final = "places.search"
TIME_LOOKUP: Final = "time.lookup"


@dataclass(frozen=True)
class LiveToolDefinition:
    name: str
    args_model: Type[LiveToolArgs]
    category: str


TOOLS: Final[dict[str, LiveToolDefinition]] = {
    WEATHER_CURRENT: LiveToolDefinition(WEATHER_CURRENT, WeatherArgs, "weather"),
    WEATHER_FORECAST: LiveToolDefinition(WEATHER_FORECAST, WeatherArgs, "weather"),
    NEWS_SEARCH: LiveToolDefinition(NEWS_SEARCH, NewsArgs, "news"),
    WEB_SEARCH: LiveToolDefinition(WEB_SEARCH, WebSearchArgs, "web"),
    MARKET_QUOTE: LiveToolDefinition(MARKET_QUOTE, MarketArgs, "markets"),
    CRYPTO_QUOTE: LiveToolDefinition(CRYPTO_QUOTE, MarketArgs, "markets"),
    SPORTS_LOOKUP: LiveToolDefinition(SPORTS_LOOKUP, SportsArgs, "sports"),
    PLACES_SEARCH: LiveToolDefinition(PLACES_SEARCH, PlacesArgs, "places"),
    TIME_LOOKUP: LiveToolDefinition(TIME_LOOKUP, TimeArgs, "time"),
}
TOOL_NAMES: Final[tuple[str, ...]] = tuple(TOOLS)
ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(TOOL_NAMES)


def is_allowed(tool_name: str) -> bool:
    return tool_name in ALLOWED_TOOLS


def validate_args(tool_name: str, arguments: dict[str, object]) -> LiveToolArgs:
    definition = TOOLS[tool_name]
    return definition.args_model.model_validate(arguments)
