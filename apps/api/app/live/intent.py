"""Deterministic live-intent routing."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.time import system_clock
from app.live import registry
from app.live.schemas import (
    MarketArgs,
    NewsArgs,
    PlacesArgs,
    SportsArgs,
    TimeArgs,
    WeatherArgs,
    WebSearchArgs,
)
from app.live.temporal import (
    TemporalInterpretation,
    has_ambiguous_temporal_language,
    interpret_temporal,
    is_temporal_follow_up,
    strip_trailing_temporal_phrase,
)

_WEATHER_RE = re.compile(
    r"\bwhat(?:'s| is| was)\s+(?:the\s+)?(?:weather|temperature|forecast)\b|"
    r"\b(?:weather|temperature|forecast)\s+"
    r"(?:in|at|near|for|today|tonight|tomorrow|yesterday|now|current|this|last)\b|"
    r"\b(?:today(?:'s)?|tonight(?:'s)?|tomorrow(?:'s)?|yesterday(?:'s)?|current)\s+"
    r"(?:weather|temperature|forecast)\b|"
    r"\b(?:is it raining|will it rain|rain(?:ing)?\s+"
    r"(?:today|tonight|tomorrow|now))\b",
    re.I,
)
_NEWS_RE = re.compile(
    r"\b(?:latest|recent|today(?:'s)?|this week|current|happening|happened)\b.*\b(?:news|events?|india|ai|cybersecurity)\b|"
    r"\b(?:latest|recent|today(?:'s)?|this week|current)\s+(?:ai|cybersecurity|india)\s+news\b|"
    r"\bwhat(?:'s| is) happening\b",
    re.I,
)
_MARKET_RE = re.compile(
    r"\b(?:stock price|trading at|market doing|quote)\b|"
    r"\bwhat was\s+(?:[A-Z]{1,5}|bitcoin|btc|ethereum|eth|the market)\s+"
    r"(?:trading\s+)?at\b|"
    r"\b(?:current|latest|today(?:'s)?|now)\b.{0,80}"
    r"\b(?:price|markets?|stocks?|bitcoin|btc|crypto)\b|"
    r"\b(?:price|markets?|stocks?|bitcoin|btc|crypto)\b.{0,80}"
    r"\b(?:current|latest|today(?:'s)?|now)\b|"
    r"\bmarkets?\b.*\b(?:open|closed)\b|"
    r"\bhow (?:is|are) (?:the )?markets?\b",
    re.I,
)
_SPORTS_RE = re.compile(r"\b(?:score|match|won|fixture|standings|game)\b", re.I)
_PLACES_RE = re.compile(
    r"\b(?:restaurants?|coffee|cafes?|hospitals?|nearby|near me|places?)\b", re.I
)
_TIME_RE = re.compile(r"\b(?:what time|time in|date in|timezone)\b", re.I)
_WEB_CURRENT_RE = re.compile(
    r"\bcurrent\s+(?:ceo|president|version|release|status)\b|"
    r"\b(?:latest|newest)\s+(?:on|about|version|release|update|news)\b|"
    r"\bnew version\b|\blatest version\b|\bis .* down\b|\bceo of\b",
    re.I,
)

_PRIVATE_OWNERSHIP_RE = re.compile(
    r"\b(?:my|mine)\b|\b(?:do|did)\s+i\s+have\b|\bfor me\b",
    re.I,
)
_PRIVATE_DOMAIN_RE = re.compile(
    r"\b(?:tasks?|projects?|reminders?|notes?|lists?|notifications?)\b",
    re.I,
)

_PRICE_FOLLOW_UP_RE = re.compile(
    r"what(?:'s| is)\s+the\s+price\s+now", re.I
)
_PLACE_FOLLOW_UP_RE = re.compile(
    r"(?:show|find)(?:\s+me)?\s+another(?:\s+one)?\s+(?:nearby|near me)", re.I
)
_RAIN_FOLLOW_UP_RE = re.compile(r"is\s+it\s+still\s+raining", re.I)
_TEAM_FOLLOW_UP_RE = re.compile(r"(?:what|how)\s+about\s+that\s+team", re.I)
_OPEN_NOW_FOLLOW_UP_RE = re.compile(r"is\s+it\s+open\s+(?:right\s+)?now", re.I)
_SUBJECT_FOLLOW_UP_RE = re.compile(
    r"(?:what|how)\s+about\s+([A-Za-z][A-Za-z .'-]{1,80})", re.I
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
    temporal: TemporalInterpretation | None = None


@dataclass(frozen=True)
class LiveRequestResolution:
    intent: LiveIntent | None = None
    clarification: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class LiveFollowUpResolution:
    intent: LiveIntent | None = None
    clarification: str | None = None
    category: str | None = None
    explicit_override: bool = False


def resolve_live_intent(message: str) -> LiveIntent | None:
    """Compatibility wrapper using the system clock and Rocky's UTC fallback."""

    temporal = interpret_temporal(
        message,
        clock=system_clock,
        timezone_name="UTC",
    )
    resolution = resolve_live_request(message, temporal=temporal)
    return resolution.intent if resolution is not None else None


def resolve_live_request(
    message: str,
    *,
    temporal: TemporalInterpretation | None,
) -> LiveRequestResolution | None:
    text = message.strip()
    if _looks_like_private_domain_request(text):
        return None
    if _is_follow_up_only(text, temporal):
        return None

    if _WEATHER_RE.search(text):
        if has_ambiguous_temporal_language(text):
            return LiveRequestResolution(
                clarification="Which weather time window do you mean?",
                category="weather",
            )
        location = _extract_location(text)
        window = _weather_window_for(temporal, text)
        if window is None:
            historical = temporal is not None and temporal.relative_label in {
                "yesterday",
                "last_night",
            }
            detail = (
                "Historical weather isn't supported yet."
                if historical
                else "That weather time window isn't supported yet."
            )
            return LiveRequestResolution(
                clarification=detail,
                category="weather",
            )
        tool = registry.WEATHER_CURRENT if window == "current" else registry.WEATHER_FORECAST
        return LiveRequestResolution(
            intent=LiveIntent(
                tool,
                {"location": location or "", "window": window},
                temporal,
            ),
            category="weather",
        )

    if _TIME_RE.search(text):
        location = _extract_time_location(text)
        if location is None:
            return None
        return LiveRequestResolution(
            intent=LiveIntent(
                registry.TIME_LOOKUP,
                {"location": location},
                temporal,
            ),
            category="time",
        )

    if _MARKET_RE.search(text):
        if has_ambiguous_temporal_language(text):
            return LiveRequestResolution(
                clarification="Which market time window do you mean?",
                category="markets",
            )
        symbol, asset_type = _market_symbol(text)
        if symbol == "SPY" and asset_type == "index" and not re.search(
            r"\b(?:market|markets|s&p|sp500)\b", text, re.I
        ):
            return None
        if temporal is not None and temporal.relative_label not in {
            "now",
            "today",
            "latest",
        }:
            return LiveRequestResolution(
                clarification="Historical and future market quotes aren't supported yet.",
                category="markets",
            )
        return LiveRequestResolution(
            intent=LiveIntent(
                registry.CRYPTO_QUOTE if asset_type == "crypto" else registry.MARKET_QUOTE,
                {"symbol": symbol, "asset_type": asset_type},
                temporal,
            ),
            category="markets",
        )

    if _NEWS_RE.search(text):
        if temporal is not None and temporal.relative_label not in {
            "now",
            "today",
            "this_week",
            "recent",
            "latest",
        }:
            return LiveRequestResolution(
                clarification="That news time window isn't supported yet.",
                category="news",
            )
        arguments: dict[str, object] = {
            "query": _news_query(text),
            "max_results": 5,
        }
        if (
            temporal is not None
            and temporal.relative_label in {"today", "this_week"}
            and temporal.start_at is not None
        ):
            arguments["published_after"] = temporal.start_at
        return LiveRequestResolution(
            intent=LiveIntent(
                registry.NEWS_SEARCH,
                arguments,
                temporal,
            ),
            category="news",
        )

    if _SPORTS_RE.search(text):
        if temporal is not None and temporal.relative_label in {
            "yesterday",
            "last_night",
            "this_morning",
            "this_afternoon",
            "this_evening",
        }:
            return LiveRequestResolution(
                clarification="Historical sports lookup isn't supported yet.",
                category="sports",
            )
        lookup = "score" if temporal and temporal.relative_label == "now" else "latest"
        return LiveRequestResolution(
            intent=LiveIntent(
                registry.SPORTS_LOOKUP,
                {"query": _clean_question(text), "lookup": lookup, "max_results": 3},
                temporal,
            ),
            category="sports",
        )

    if _PLACES_RE.search(text):
        if temporal is not None and re.search(r"\bopen\b", text, re.I):
            return LiveRequestResolution(
                clarification="Open-now filtering isn't supported for places yet.",
                category="places",
            )
        location = _extract_location(text)
        if location is None and _HERE_RE.search(text):
            return LiveRequestResolution(
                intent=LiveIntent(
                    registry.PLACES_SEARCH,
                    {"query": _place_query(text), "location": ""},
                    temporal,
                ),
                category="places",
            )
        if location is not None:
            return LiveRequestResolution(
                intent=LiveIntent(
                    registry.PLACES_SEARCH,
                    {"query": _place_query(text), "location": location, "max_results": 5},
                    temporal,
                ),
                category="places",
            )

    if _WEB_CURRENT_RE.search(text):
        return LiveRequestResolution(
            intent=LiveIntent(
                registry.WEB_SEARCH,
                {"query": _clean_question(text), "max_results": 3},
                temporal,
            ),
            category="web",
        )

    return None


def canonical_live_reference(
    intent: LiveIntent,
) -> tuple[str, dict[str, object]]:
    """Return an allowlisted, coordinate-free durable representation."""

    args = registry.validate_args(intent.tool_name, intent.arguments)
    category = registry.TOOLS[intent.tool_name].category
    metadata: dict[str, object] = {
        "tool": intent.tool_name,
        "category": category,
    }

    if isinstance(args, WeatherArgs):
        metadata.update(location=args.location, window=args.window)
        return args.location, metadata
    if isinstance(args, MarketArgs):
        metadata.update(symbol=args.symbol, asset_type=args.asset_type)
        return args.symbol, metadata
    if isinstance(args, NewsArgs):
        metadata["query"] = args.query
        return args.query, metadata
    if isinstance(args, WebSearchArgs):
        metadata["query"] = args.query
        return args.query, metadata
    if isinstance(args, SportsArgs):
        metadata.update(query=args.query, lookup=args.lookup)
        return args.query, metadata
    if isinstance(args, PlacesArgs):
        metadata.update(query=args.query, location=args.location)
        return args.query, metadata
    if isinstance(args, TimeArgs):
        metadata["location"] = args.location
        return args.location, metadata
    raise ValueError("Unsupported live context")


def resolve_live_follow_up(
    message: str,
    *,
    live_subject: Mapping[str, object] | None,
    place: Mapping[str, object] | None,
    temporal: TemporalInterpretation | None = None,
) -> LiveFollowUpResolution | None:
    """Resolve only bounded, category-compatible references to prior live state."""

    text = message.strip(" ?.\t\n")
    if temporal is None:
        temporal = interpret_temporal(
            text,
            clock=system_clock,
            timezone_name="UTC",
        )
    if _looks_like_private_domain_request(text):
        return None

    if _PLACE_FOLLOW_UP_RE.fullmatch(text):
        place_context = (
            live_subject
            if _context_tool(live_subject) == registry.PLACES_SEARCH
            else place if live_subject is None else None
        )
        query = _context_string(place_context, "query")
        location = _context_string(place_context, "location")
        if not query or not location:
            return LiveFollowUpResolution(
                clarification="What kind of place should I look for nearby?",
                category="places",
            )
        return LiveFollowUpResolution(
            intent=LiveIntent(
                registry.PLACES_SEARCH,
                {
                    "query": query,
                    "location": "" if location == "Current location" else location,
                    "max_results": 5,
                },
            ),
            category="places",
        )

    if _PRICE_FOLLOW_UP_RE.fullmatch(text):
        return _market_follow_up(live_subject, temporal=temporal)

    if _TEAM_FOLLOW_UP_RE.fullmatch(text):
        query = _context_string(live_subject, "query")
        lookup = _context_string(live_subject, "lookup") or "latest"
        if _context_tool(live_subject) != registry.SPORTS_LOOKUP or not query:
            return LiveFollowUpResolution(
                clarification="Which team do you mean?",
                category="sports",
            )
        return LiveFollowUpResolution(
            intent=LiveIntent(
                registry.SPORTS_LOOKUP,
                {"query": query, "lookup": lookup, "max_results": 3},
            ),
            category="sports",
        )

    if _OPEN_NOW_FOLLOW_UP_RE.fullmatch(text):
        if _context_tool(live_subject) == registry.PLACES_SEARCH:
            return LiveFollowUpResolution(
                clarification="Open-now filtering isn't supported for places yet.",
                category="places",
            )
        return LiveFollowUpResolution(
            clarification="Which place do you mean?",
            category="places",
        )

    if _RAIN_FOLLOW_UP_RE.fullmatch(text):
        return _weather_follow_up(live_subject, window="current", temporal=temporal)

    if is_temporal_follow_up(text, temporal):
        assert temporal is not None
        label = temporal.relative_label
        tool = _context_tool(live_subject)
        if label == "now" and tool in {
            registry.MARKET_QUOTE,
            registry.CRYPTO_QUOTE,
        }:
            return _market_follow_up(live_subject, temporal=temporal)
        if tool in {registry.WEATHER_CURRENT, registry.WEATHER_FORECAST}:
            window = _weather_window_for(temporal, text)
            if window is None:
                return LiveFollowUpResolution(
                    clarification="Historical weather and extended weather windows aren't supported yet.",
                    category="weather",
                )
            return _weather_follow_up(
                live_subject,
                window=window,
                temporal=temporal,
            )
        if tool in {registry.MARKET_QUOTE, registry.CRYPTO_QUOTE}:
            return LiveFollowUpResolution(
                clarification="I can continue with a current quote, but not that time window.",
                category="markets",
            )
        return LiveFollowUpResolution(
            clarification="Which live topic and location do you mean?",
        )

    subject_match = _SUBJECT_FOLLOW_UP_RE.fullmatch(text)
    if not subject_match:
        return None
    subject = subject_match.group(1).strip()
    tool = _context_tool(live_subject)

    if tool in {registry.MARKET_QUOTE, registry.CRYPTO_QUOTE}:
        market_subject = _explicit_market_subject(subject)
        if market_subject is not None:
            symbol, asset_type = market_subject
            return LiveFollowUpResolution(
                intent=LiveIntent(
                    registry.CRYPTO_QUOTE if asset_type == "crypto" else registry.MARKET_QUOTE,
                    {"symbol": symbol, "asset_type": asset_type},
                ),
                category="markets",
                explicit_override=True,
            )

    if tool in {registry.WEATHER_CURRENT, registry.WEATHER_FORECAST}:
        window = _context_string(live_subject, "window") or "current"
        return LiveFollowUpResolution(
            intent=LiveIntent(
                registry.WEATHER_CURRENT if window == "current" else registry.WEATHER_FORECAST,
                {"location": subject, "window": window},
            ),
            category="weather",
            explicit_override=True,
        )

    if tool == registry.TIME_LOOKUP:
        return LiveFollowUpResolution(
            intent=LiveIntent(registry.TIME_LOOKUP, {"location": subject}),
            category="time",
            explicit_override=True,
        )

    return None


def _weather_follow_up(
    context: Mapping[str, object] | None,
    *,
    window: str,
    temporal: TemporalInterpretation | None,
) -> LiveFollowUpResolution:
    location = _context_string(context, "location")
    if _context_tool(context) not in {
        registry.WEATHER_CURRENT,
        registry.WEATHER_FORECAST,
    } or not location:
        return LiveFollowUpResolution(
            clarification="Which location do you mean for the weather?",
            category="weather",
        )
    return LiveFollowUpResolution(
        intent=LiveIntent(
            registry.WEATHER_CURRENT if window == "current" else registry.WEATHER_FORECAST,
            {
                "location": "" if location == "Current location" else location,
                "window": window,
            },
            temporal,
        ),
        category="weather",
    )


def _market_follow_up(
    context: Mapping[str, object] | None,
    *,
    temporal: TemporalInterpretation | None = None,
) -> LiveFollowUpResolution:
    tool = _context_tool(context)
    symbol = _context_string(context, "symbol")
    asset_type = _context_string(context, "asset_type")
    if tool not in {registry.MARKET_QUOTE, registry.CRYPTO_QUOTE} or not (
        symbol and asset_type
    ):
        return LiveFollowUpResolution(
            clarification="Which stock or cryptocurrency price should I look up?",
            category="markets",
        )
    return LiveFollowUpResolution(
        intent=LiveIntent(
            tool,
            {"symbol": symbol, "asset_type": asset_type},
            temporal,
        ),
        category="markets",
    )


def _context_tool(context: Mapping[str, object] | None) -> str | None:
    tool = _context_string(context, "tool")
    return tool if tool and registry.is_allowed(tool) else None


def _context_string(
    context: Mapping[str, object] | None, key: str
) -> str | None:
    value = context.get(key) if context else None
    return value if isinstance(value, str) and value.strip() else None


def _is_follow_up_only(
    text: str, temporal: TemporalInterpretation | None
) -> bool:
    cleaned = text.strip(" ?.\t\n")
    return is_temporal_follow_up(cleaned, temporal) or any(
        pattern.fullmatch(text.strip(" ?.\t\n"))
        for pattern in (
            _PRICE_FOLLOW_UP_RE,
            _PLACE_FOLLOW_UP_RE,
            _RAIN_FOLLOW_UP_RE,
            _TEAM_FOLLOW_UP_RE,
            _OPEN_NOW_FOLLOW_UP_RE,
        )
    )


def _looks_like_private_domain_request(text: str) -> bool:
    """Keep Rocky-owned nouns private when freshness words are incidental."""

    return bool(
        _PRIVATE_OWNERSHIP_RE.search(text) and _PRIVATE_DOMAIN_RE.search(text)
    )


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
    cleaned = strip_trailing_temporal_phrase(value.strip(" ?."))
    return re.sub(r"\bplease\b.*$", "", cleaned, flags=re.I).strip(" ?.")


def _weather_window_for(
    temporal: TemporalInterpretation | None, text: str
) -> str | None:
    if temporal is None:
        return "short" if "forecast" in text.lower() else "current"
    return {
        "now": "current",
        "today": "today",
        "tonight": "tonight",
        "tomorrow": "tomorrow",
    }.get(temporal.relative_label)


def _market_symbol(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for cue, symbol in _SYMBOLS.items():
        if cue in lowered:
            return symbol, "crypto" if symbol in {"BTC", "ETH"} else "stock"
    ticker = re.search(r"\b[A-Z]{1,5}\b", text)
    return (ticker.group(0), "stock") if ticker else ("SPY", "index")


def _explicit_market_subject(text: str) -> tuple[str, str] | None:
    lowered = text.lower().strip()
    for cue, symbol in _SYMBOLS.items():
        if cue in lowered:
            return symbol, "crypto" if symbol in {"BTC", "ETH"} else "stock"
    ticker = re.fullmatch(r"[A-Z]{1,5}", text.strip())
    return (ticker.group(0), "stock") if ticker else None


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
