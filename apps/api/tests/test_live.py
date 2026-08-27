"""Live-intelligence subsystem tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.live import registry
from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveUnsupportedRequest
from app.live.intent import LiveIntent, resolve_live_intent
from app.live.providers.time_provider import LocalTimeProvider
from app.live.responder import render_live_result
from app.live.schemas import (
    MarketArgs,
    MarketQuote,
    NewsArgs,
    NewsArticle,
    NewsReport,
    SourceMetadata,
    WeatherArgs,
    WeatherReport,
)
from app.live.service import LiveIntelligenceService, LiveLookupResult


class _WeatherProvider:
    def __init__(self, result: WeatherReport | Exception) -> None:
        self.result = result
        self.calls: list[WeatherArgs] = []

    async def weather(self, args: WeatherArgs) -> WeatherReport:
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _NewsProvider:
    async def search_news(self, args: NewsArgs) -> NewsReport:
        return NewsReport(
            query=args.query,
            articles=(
                NewsArticle(
                    title="AI lab ships model",
                    source_name="Example News",
                    published_at=datetime.now(timezone.utc),
                ),
            ),
            source=SourceMetadata(
                provider="fake-news",
                retrieved_at=datetime.now(timezone.utc),
                freshness="test",
            ),
        )


class _MarketProvider:
    async def quote(self, args: MarketArgs) -> MarketQuote:
        return MarketQuote(
            symbol=args.symbol,
            asset_type=args.asset_type,
            price=101.5,
            currency="USD",
            delayed=True,
            source=SourceMetadata(
                provider="fake-market",
                retrieved_at=datetime.now(timezone.utc),
                freshness="test",
            ),
        )


def _weather_report() -> WeatherReport:
    return WeatherReport(
        location="London, United Kingdom",
        window="current",
        temperature_c=18.0,
        condition="overcast",
        source=SourceMetadata(
            provider="fake-weather",
            retrieved_at=datetime.now(timezone.utc),
            freshness="test",
        ),
    )


def test_live_intent_routes_weather_news_market_time_and_web() -> None:
    assert resolve_live_intent("What's the weather in London?").tool_name == (
        registry.WEATHER_CURRENT
    )
    assert resolve_live_intent("Latest AI news").tool_name == registry.NEWS_SEARCH
    assert resolve_live_intent("What's Apple trading at?").tool_name == (
        registry.MARKET_QUOTE
    )
    assert resolve_live_intent("What time is it in Tokyo?").tool_name == (
        registry.TIME_LOOKUP
    )
    assert resolve_live_intent("Who is the current CEO of X?").tool_name == (
        registry.WEB_SEARCH
    )


def test_evergreen_question_has_no_live_intent() -> None:
    assert resolve_live_intent("What is photosynthesis?") is None


def test_live_unconfigured_error_is_user_friendly() -> None:
    reply = render_live_result(
        LiveLookupResult(
            registry.NEWS_SEARCH,
            None,
            error_code="provider_unavailable",
            error_message="News search is not configured.",
        )
    )

    assert reply == "Live news isn't configured yet."
    assert "NEWS_API_KEY" not in reply


def test_registry_rejects_unknown_or_injected_arguments() -> None:
    with pytest.raises(KeyError):
        registry.validate_args("run_shell", {"cmd": "date"})

    with pytest.raises(ValueError):
        registry.validate_args(
            registry.WEATHER_CURRENT,
            {"location": "London", "window": "current", "url": "https://example.com"},
        )


@pytest.mark.asyncio
async def test_weather_success_returns_source_metadata() -> None:
    provider = _WeatherProvider(_weather_report())
    service = LiveIntelligenceService(weather_provider=provider)

    result = await service.execute(
        LiveIntent(
            registry.WEATHER_CURRENT,
            {"location": "London", "window": "current"},
        )
    )

    assert result.succeeded is True
    assert isinstance(result.report, WeatherReport)
    assert result.report.source.provider == "fake-weather"
    assert provider.calls[0].location == "London"


@pytest.mark.asyncio
async def test_provider_unavailable_and_timeout_are_clean_results() -> None:
    missing = LiveIntelligenceService()
    unavailable = await missing.execute(
        LiveIntent(registry.NEWS_SEARCH, {"query": "India", "max_results": 3})
    )
    assert unavailable.succeeded is False
    assert unavailable.error_code == "provider_unavailable"

    timeout_provider = _WeatherProvider(LiveProviderTimeout("Weather provider timed out."))
    service = LiveIntelligenceService(weather_provider=timeout_provider)
    timed_out = await service.execute(
        LiveIntent(registry.WEATHER_CURRENT, {"location": "London", "window": "current"})
    )
    assert timed_out.succeeded is False
    assert timed_out.error_code == "provider_timeout"


@pytest.mark.asyncio
async def test_malformed_provider_result_is_clean_result() -> None:
    provider = _WeatherProvider(LiveMalformedResult("Weather result malformed."))
    service = LiveIntelligenceService(weather_provider=provider)

    result = await service.execute(
        LiveIntent(registry.WEATHER_CURRENT, {"location": "London", "window": "current"})
    )

    assert result.succeeded is False
    assert result.error_code == "malformed_result"


@pytest.mark.asyncio
async def test_unsupported_location_needs_explicit_trusted_location() -> None:
    service = LiveIntelligenceService(weather_provider=_WeatherProvider(_weather_report()))

    result = await service.execute(
        LiveIntent(registry.WEATHER_CURRENT, {"location": "", "window": "current"})
    )

    assert result.succeeded is False
    assert result.error_code == "invalid_arguments"
    assert result.error_message == "I need an explicit location for that live lookup."


@pytest.mark.asyncio
async def test_news_and_market_normalization_use_bounded_results() -> None:
    service = LiveIntelligenceService(
        news_provider=_NewsProvider(),
        market_provider=_MarketProvider(),
    )

    news = await service.execute(
        LiveIntent(registry.NEWS_SEARCH, {"query": "AI", "max_results": 1})
    )
    quote = await service.execute(
        LiveIntent(registry.MARKET_QUOTE, {"symbol": "aapl", "asset_type": "stock"})
    )

    assert isinstance(news.report, NewsReport)
    assert len(news.report.articles) == 1
    assert isinstance(quote.report, MarketQuote)
    assert quote.report.symbol == "AAPL"
    assert quote.report.source.provider == "fake-market"


@pytest.mark.asyncio
async def test_time_lookup_uses_timezone_aware_computation() -> None:
    service = LiveIntelligenceService(time_provider=LocalTimeProvider())

    result = await service.execute(
        LiveIntent(registry.TIME_LOOKUP, {"location": "Tokyo"})
    )

    assert result.succeeded is True
    assert result.report is not None
    assert result.report.source.provider == "python-zoneinfo"


@pytest.mark.asyncio
async def test_time_lookup_rejects_unknown_location() -> None:
    service = LiveIntelligenceService(time_provider=LocalTimeProvider())

    result = await service.execute(
        LiveIntent(registry.TIME_LOOKUP, {"location": "Atlantis"})
    )

    assert result.succeeded is False
    assert result.error_code == "unsupported_request"
