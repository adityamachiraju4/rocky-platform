"""Live-intelligence orchestration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from app.live import registry
from app.live.errors import LiveError, LiveProviderUnavailable, LiveUnsupportedRequest
from app.live.intent import LiveIntent, resolve_live_intent
from app.live.providers.base import (
    MarketProvider,
    NewsProvider,
    PlacesProvider,
    SportsProvider,
    TimeProvider,
    WeatherProvider,
    WebSearchProvider,
)
from app.live.schemas import (
    LiveReport,
    MarketArgs,
    NewsArgs,
    PlacesArgs,
    SportsArgs,
    TimeArgs,
    WeatherArgs,
    WebSearchArgs,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveLookupResult:
    tool_name: str
    report: LiveReport | None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.report is not None


class LiveIntelligenceService:
    def __init__(
        self,
        *,
        weather_provider: WeatherProvider | None = None,
        news_provider: NewsProvider | None = None,
        web_provider: WebSearchProvider | None = None,
        market_provider: MarketProvider | None = None,
        sports_provider: SportsProvider | None = None,
        places_provider: PlacesProvider | None = None,
        time_provider: TimeProvider | None = None,
    ) -> None:
        self._weather_provider = weather_provider
        self._news_provider = news_provider
        self._web_provider = web_provider
        self._market_provider = market_provider
        self._sports_provider = sports_provider
        self._places_provider = places_provider
        self._time_provider = time_provider

    def resolve(self, message: str) -> LiveIntent | None:
        return resolve_live_intent(message)

    async def execute(self, intent: LiveIntent) -> LiveLookupResult:
        if not registry.is_allowed(intent.tool_name):
            return LiveLookupResult(
                intent.tool_name,
                None,
                error_code="unknown_tool",
                error_message="That live lookup is not registered.",
            )
        try:
            args = registry.validate_args(intent.tool_name, intent.arguments)
            report = await self._dispatch(intent.tool_name, args)
            return LiveLookupResult(intent.tool_name, report)
        except ValidationError as exc:
            return LiveLookupResult(
                intent.tool_name,
                None,
                error_code="invalid_arguments",
                error_message=_validation_message(intent.tool_name, exc),
            )
        except LiveError as exc:
            logger.info(
                "Live lookup failed: tool=%s error_code=%s",
                intent.tool_name,
                exc.code,
            )
            return LiveLookupResult(
                intent.tool_name,
                None,
                error_code=exc.code,
                error_message=str(exc),
            )

    async def _dispatch(self, tool_name: str, args: object) -> LiveReport:
        if tool_name in {registry.WEATHER_CURRENT, registry.WEATHER_FORECAST}:
            if self._weather_provider is None:
                raise LiveProviderUnavailable("Weather is not configured.")
            assert isinstance(args, WeatherArgs)
            return await self._weather_provider.weather(args)

        if tool_name == registry.NEWS_SEARCH:
            if self._news_provider is None:
                raise LiveProviderUnavailable("News search is not configured.")
            assert isinstance(args, NewsArgs)
            return await self._news_provider.search_news(args)

        if tool_name == registry.WEB_SEARCH:
            if self._web_provider is None:
                raise LiveProviderUnavailable("Current web search is not configured.")
            assert isinstance(args, WebSearchArgs)
            return await self._web_provider.search_web(args)

        if tool_name in {registry.MARKET_QUOTE, registry.CRYPTO_QUOTE}:
            if self._market_provider is None:
                raise LiveProviderUnavailable("Market quotes are not configured.")
            assert isinstance(args, MarketArgs)
            return await self._market_provider.quote(args)

        if tool_name == registry.SPORTS_LOOKUP:
            if self._sports_provider is None:
                raise LiveProviderUnavailable("Sports lookup is not configured.")
            assert isinstance(args, SportsArgs)
            return await self._sports_provider.lookup(args)

        if tool_name == registry.PLACES_SEARCH:
            if self._places_provider is None:
                raise LiveProviderUnavailable("Places search is not configured.")
            assert isinstance(args, PlacesArgs)
            return await self._places_provider.search_places(args)

        if tool_name == registry.TIME_LOOKUP:
            if self._time_provider is None:
                raise LiveProviderUnavailable("Time lookup is not configured.")
            assert isinstance(args, TimeArgs)
            return await self._time_provider.lookup_time(args)

        raise LiveUnsupportedRequest("That live lookup is not supported yet.")


def _validation_message(tool_name: str, exc: ValidationError) -> str:
    missing_location = any(
        error.get("loc") == ("location",) for error in exc.errors()
    )
    if missing_location and tool_name in {
        registry.WEATHER_CURRENT,
        registry.WEATHER_FORECAST,
        registry.PLACES_SEARCH,
    }:
        return "I need an explicit location for that live lookup."
    return "That live lookup needs cleaner, bounded arguments."


def retrieved_now() -> datetime:
    return datetime.now(timezone.utc)
