"""Deterministic live-intent routing."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.live import registry

_WEATHER_RE = re.compile(r"\b(weather|temperature|forecast|rain|raining)\b", re.I)
_NEWS_RE = re.compile(
    r"\b(?:latest|today(?:'s)?|current|happening|happened)\b.*\b(?:news|events?|india|ai|cybersecurity)\b|"
    r"\b(?:latest|today(?:'s)?|current)\s+(?:ai|cybersecurity|india)\s+news\b|"
    r"\bwhat(?:'s| is) happening\b",
    re.I,
)
_MARKET_RE = re.compile(
    r"\b(?:stock price|trading at|bitcoin|btc|crypto|market doing|markets?|quote)\b",
    re.I,
)
_SPORTS_RE = re.compile(r"\b(?:score|match|won|fixture|standings|game)\b", re.I)
_PLACES_RE = re.compile(
    r"\b(?:restaurants?|coffee|cafes?|hospitals?|nearby|near me|places?)\b", re.I
)
_TIME_RE = re.compile(r"\b(?:what time|time in|date in|timezone)\b", re.I)
_WEB_CURRENT_RE = re.compile(
    r"\b(?:current|latest|newest|new version|latest version|is .* down|ceo of)\b",
    re.I,
)

_HERE_RE = re.compile(r"\b(?:here|near me|nearby|my location)\b", re.I)
_LOCATION_AFTER_IN_RE = re.compile(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,80})", re.I)
_TIME_LOCATION_RE = re.compile(r"\b(?:in|at)\s+([A-Za-z][A-Za-z_ /.-]{1,80})", re.I)

_SYMBOLS = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "s&p": "SPY",
    "sp500": "SPY",
    "market": "SPY",
}


@dataclass(frozen=True)
class LiveIntent:
    tool_name: str
    arguments: dict[str, object]


def resolve_live_intent(message: str) -> LiveIntent | None:
    text = message.strip()
    if _WEATHER_RE.search(text):
        location = _extract_location(text)
        if location is None:
            return LiveIntent(registry.WEATHER_CURRENT, {"location": "", "window": "current"})
        window = _weather_window(text)
        tool = registry.WEATHER_CURRENT if window == "current" else registry.WEATHER_FORECAST
        return LiveIntent(tool, {"location": location, "window": window})

    if _TIME_RE.search(text):
        location = _extract_time_location(text)
        if location is None:
            return None
        return LiveIntent(registry.TIME_LOOKUP, {"location": location})

    if _MARKET_RE.search(text):
        symbol, asset_type = _market_symbol(text)
        return LiveIntent(
            registry.CRYPTO_QUOTE if asset_type == "crypto" else registry.MARKET_QUOTE,
            {"symbol": symbol, "asset_type": asset_type},
        )

    if _NEWS_RE.search(text):
        return LiveIntent(
            registry.NEWS_SEARCH,
            {"query": _news_query(text), "max_results": 5},
        )

    if _SPORTS_RE.search(text):
        return LiveIntent(
            registry.SPORTS_LOOKUP,
            {"query": _clean_question(text), "lookup": "latest", "max_results": 3},
        )

    if _PLACES_RE.search(text):
        location = _extract_location(text)
        if location is None and _HERE_RE.search(text):
            return LiveIntent(registry.PLACES_SEARCH, {"query": _place_query(text), "location": ""})
        if location is not None:
            return LiveIntent(
                registry.PLACES_SEARCH,
                {"query": _place_query(text), "location": location, "max_results": 5},
            )

    if _WEB_CURRENT_RE.search(text):
        return LiveIntent(
            registry.WEB_SEARCH,
            {"query": _clean_question(text), "max_results": 3},
        )

    return None


def _extract_location(text: str) -> str | None:
    if _HERE_RE.search(text):
        return None
    match = _LOCATION_AFTER_IN_RE.search(text)
    if not match:
        return None
    return _trim_location(match.group(1))


def _extract_time_location(text: str) -> str | None:
    match = _TIME_LOCATION_RE.search(text)
    return _trim_location(match.group(1)) if match else None


def _trim_location(value: str) -> str:
    return re.sub(
        r"\b(?:today|tomorrow|tonight|now|please|right now)\b.*$",
        "",
        value.strip(" ?."),
        flags=re.I,
    ).strip(" ?.")


def _weather_window(text: str) -> str:
    lowered = text.lower()
    if "tomorrow" in lowered:
        return "tomorrow"
    if "tonight" in lowered:
        return "tonight"
    if "today" in lowered:
        return "today"
    if "forecast" in lowered:
        return "short"
    return "current"


def _market_symbol(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for cue, symbol in _SYMBOLS.items():
        if cue in lowered:
            return symbol, "crypto" if symbol in {"BTC", "ETH"} else "stock"
    ticker = re.search(r"\b[A-Z]{1,5}\b", text)
    return (ticker.group(0), "stock") if ticker else ("SPY", "index")


def _news_query(text: str) -> str:
    cleaned = _clean_question(text)
    lowered = cleaned.lower()
    if "ai" in lowered:
        return "AI"
    if "cybersecurity" in lowered:
        return "cybersecurity"
    if "india" in lowered:
        return "India"
    return cleaned


def _place_query(text: str) -> str:
    lowered = text.lower()
    if "coffee" in lowered or "cafe" in lowered:
        return "coffee"
    if "hospital" in lowered:
        return "hospital"
    if "restaurant" in lowered:
        return "restaurant"
    return _clean_question(text)


def _clean_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip(" ?.")).strip()
