"""Provider protocols for live-information categories."""
from __future__ import annotations

from typing import Protocol

from app.live.schemas import (
    MarketArgs,
    MarketQuote,
    NewsArgs,
    NewsReport,
    PlacesArgs,
    PlacesReport,
    SportsArgs,
    SportsReport,
    TimeArgs,
    TimeReport,
    WeatherArgs,
    WeatherReport,
    WebReport,
    WebSearchArgs,
)


class WeatherProvider(Protocol):
    async def weather(self, args: WeatherArgs) -> WeatherReport: ...


class NewsProvider(Protocol):
    async def search_news(self, args: NewsArgs) -> NewsReport: ...


class WebSearchProvider(Protocol):
    async def search_web(self, args: WebSearchArgs) -> WebReport: ...


class MarketProvider(Protocol):
    async def quote(self, args: MarketArgs) -> MarketQuote: ...


class SportsProvider(Protocol):
    async def lookup(self, args: SportsArgs) -> SportsReport: ...


class PlacesProvider(Protocol):
    async def search_places(self, args: PlacesArgs) -> PlacesReport: ...


class TimeProvider(Protocol):
    async def lookup_time(self, args: TimeArgs) -> TimeReport: ...


class LiveProviders(Protocol):
    weather_provider: WeatherProvider | None
    news_provider: NewsProvider | None
    web_provider: WebSearchProvider | None
    market_provider: MarketProvider | None
    sports_provider: SportsProvider | None
    places_provider: PlacesProvider | None
    time_provider: TimeProvider | None
