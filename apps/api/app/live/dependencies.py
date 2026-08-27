"""Dependency wiring for live intelligence."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core import settings
from app.live.providers.finnhub import FinnhubMarketProvider
from app.live.providers.geoapify import GeoapifyPlacesProvider
from app.live.providers.newsapi import NewsApiProvider
from app.live.providers.open_meteo import OpenMeteoWeatherProvider
from app.live.providers.tavily import TavilyWebSearchProvider
from app.live.providers.thesportsdb import TheSportsDbProvider
from app.live.providers.time_provider import LocalTimeProvider
from app.live.service import LiveIntelligenceService


def get_live_intelligence_service() -> LiveIntelligenceService:
    timeout = settings.get_live_timeout_seconds()
    news_key = settings.get_news_api_key()
    finnhub_key = settings.get_finnhub_api_key()
    tavily_key = settings.get_tavily_api_key()
    geoapify_key = settings.get_geoapify_api_key()
    return LiveIntelligenceService(
        weather_provider=OpenMeteoWeatherProvider(timeout_seconds=timeout),
        news_provider=(
            NewsApiProvider(api_key=news_key, timeout_seconds=timeout)
            if news_key
            else None
        ),
        web_provider=(
            TavilyWebSearchProvider(api_key=tavily_key, timeout_seconds=timeout)
            if tavily_key
            else None
        ),
        market_provider=(
            FinnhubMarketProvider(api_key=finnhub_key, timeout_seconds=timeout)
            if finnhub_key
            else None
        ),
        sports_provider=TheSportsDbProvider(
            api_key=settings.get_thesportsdb_api_key(),
            timeout_seconds=timeout,
        ),
        places_provider=(
            GeoapifyPlacesProvider(api_key=geoapify_key, timeout_seconds=timeout)
            if geoapify_key
            else None
        ),
        time_provider=LocalTimeProvider(),
    )


LiveIntelligenceServiceDep = Annotated[
    LiveIntelligenceService,
    Depends(get_live_intelligence_service),
]

__all__ = ["get_live_intelligence_service", "LiveIntelligenceServiceDep"]
