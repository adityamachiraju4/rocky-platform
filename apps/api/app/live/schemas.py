"""Strict schemas and normalized live result types."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictLiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WeatherArgs(StrictLiveModel):
    location: str = Field(min_length=2, max_length=120)
    window: Literal["current", "today", "tonight", "tomorrow", "short"] = "current"
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class NewsArgs(StrictLiveModel):
    query: str = Field(min_length=2, max_length=160)
    max_results: int = Field(default=5, ge=1, le=5)


class WebSearchArgs(StrictLiveModel):
    query: str = Field(min_length=2, max_length=160)
    max_results: int = Field(default=3, ge=1, le=5)


class MarketArgs(StrictLiveModel):
    symbol: str = Field(min_length=1, max_length=16)
    asset_type: Literal["stock", "crypto", "index"] = "stock"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class SportsArgs(StrictLiveModel):
    query: str = Field(min_length=2, max_length=120)
    lookup: Literal["latest", "score", "upcoming", "team"] = "latest"
    max_results: int = Field(default=3, ge=1, le=5)


class PlacesArgs(StrictLiveModel):
    query: str = Field(min_length=2, max_length=80)
    location: str = Field(min_length=2, max_length=120)
    radius_meters: int = Field(default=3000, ge=100, le=10000)
    max_results: int = Field(default=5, ge=1, le=5)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class TimeArgs(StrictLiveModel):
    location: str = Field(min_length=2, max_length=120)


class SourceMetadata(StrictLiveModel):
    provider: str = Field(min_length=1, max_length=80)
    retrieved_at: datetime
    title: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    freshness: str | None = Field(default=None, max_length=120)


class WeatherReport(StrictLiveModel):
    location: str
    window: str
    temperature_c: float | None = None
    condition: str | None = None
    precipitation_probability: int | None = Field(default=None, ge=0, le=100)
    precipitation_mm: float | None = Field(default=None, ge=0)
    wind_kph: float | None = Field(default=None, ge=0)
    forecast_date: str | None = None
    source: SourceMetadata


class NewsArticle(StrictLiveModel):
    title: str = Field(min_length=1, max_length=240)
    source_name: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    summary: str | None = Field(default=None, max_length=500)


class NewsReport(StrictLiveModel):
    query: str
    articles: tuple[NewsArticle, ...]
    source: SourceMetadata


class WebResult(StrictLiveModel):
    title: str = Field(min_length=1, max_length=240)
    url: str | None = Field(default=None, max_length=500)
    snippet: str | None = Field(default=None, max_length=500)


class WebReport(StrictLiveModel):
    query: str
    results: tuple[WebResult, ...]
    source: SourceMetadata


class MarketQuote(StrictLiveModel):
    symbol: str
    asset_type: str
    price: float
    currency: str | None = Field(default=None, max_length=12)
    change: float | None = None
    percent_change: float | None = None
    market_time: datetime | None = None
    delayed: bool = True
    source: SourceMetadata


class SportsEvent(StrictLiveModel):
    name: str = Field(min_length=1, max_length=240)
    status: str | None = Field(default=None, max_length=80)
    start_time: datetime | None = None
    score: str | None = Field(default=None, max_length=80)
    league: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)


class SportsReport(StrictLiveModel):
    query: str
    events: tuple[SportsEvent, ...]
    source: SourceMetadata


class PlaceResult(StrictLiveModel):
    name: str = Field(min_length=1, max_length=160)
    address: str | None = Field(default=None, max_length=240)
    distance_meters: int | None = Field(default=None, ge=0)
    url: str | None = Field(default=None, max_length=500)


class PlacesReport(StrictLiveModel):
    query: str
    location: str
    places: tuple[PlaceResult, ...]
    source: SourceMetadata


class TimeReport(StrictLiveModel):
    location: str
    timezone: str
    local_time: datetime
    source: SourceMetadata


LiveToolArgs = (
    WeatherArgs
    | NewsArgs
    | WebSearchArgs
    | MarketArgs
    | SportsArgs
    | PlacesArgs
    | TimeArgs
)
LiveReport = (
    WeatherReport
    | NewsReport
    | WebReport
    | MarketQuote
    | SportsReport
    | PlacesReport
    | TimeReport
)
