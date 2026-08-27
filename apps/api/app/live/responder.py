"""Grounded live-result response rendering."""
from __future__ import annotations

from app.live.schemas import (
    MarketQuote,
    NewsReport,
    PlacesReport,
    SportsReport,
    TimeReport,
    WeatherReport,
    WebReport,
)
from app.live.service import LiveLookupResult


def render_live_result(result: LiveLookupResult) -> str:
    if result.report is None:
        return _friendly_live_error(result)

    report = result.report
    if isinstance(report, WeatherReport):
        parts = [f"{report.location}: {report.window} weather"]
        if report.condition:
            parts.append(report.condition)
        if report.temperature_c is not None:
            parts.append(f"{report.temperature_c:g} C")
        if report.precipitation_probability is not None:
            parts.append(f"{report.precipitation_probability}% precipitation chance")
        elif report.precipitation_mm is not None:
            parts.append(f"{report.precipitation_mm:g} mm precipitation")
        if report.wind_kph is not None:
            parts.append(f"wind {report.wind_kph:g} kph")
        if report.forecast_date:
            parts.append(f"for {report.forecast_date}")
        return ". ".join(parts) + f". Source: {report.source.provider}."

    if isinstance(report, NewsReport):
        items = [
            _news_line(index, article.title, article.source_name)
            for index, article in enumerate(report.articles[:5], start=1)
        ]
        return f"Latest results for {report.query}: " + " ".join(items)

    if isinstance(report, WebReport):
        items = [
            f"{index}. {item.title}" for index, item in enumerate(report.results[:3], start=1)
        ]
        return f"Current web results for {report.query}: " + " ".join(items)

    if isinstance(report, MarketQuote):
        change = ""
        if report.percent_change is not None:
            change = f" ({report.percent_change:+.2f}%)"
        delayed = " Delayed data." if report.delayed else ""
        return (
            f"{report.symbol} is {report.price:g}"
            f"{f' {report.currency}' if report.currency else ''}{change}."
            f"{delayed} Source: {report.source.provider}."
        )

    if isinstance(report, SportsReport):
        items = [
            _sports_line(index, event.name, event.score, event.status)
            for index, event in enumerate(report.events[:3], start=1)
        ]
        return f"Sports results for {report.query}: " + " ".join(items)

    if isinstance(report, PlacesReport):
        items = [
            f"{index}. {place.name}{f' - {place.address}' if place.address else ''}"
            for index, place in enumerate(report.places[:5], start=1)
        ]
        return f"Places near {report.location}: " + " ".join(items)

    if isinstance(report, TimeReport):
        return (
            f"In {report.location}, it is "
            f"{report.local_time.strftime('%Y-%m-%d %H:%M')} "
            f"({report.timezone})."
        )

    return "I got live data, but couldn't summarize it safely."


def _news_line(index: int, title: str, source: str) -> str:
    return f"{index}. {title} ({source})."


def _sports_line(
    index: int, name: str, score: str | None, status: str | None
) -> str:
    details = ", ".join(value for value in (score, status) if value)
    return f"{index}. {name}{f' ({details})' if details else ''}."


def _friendly_live_error(result: LiveLookupResult) -> str:
    category = result.tool_name.split(".", 1)[0]
    if result.error_code == "invalid_arguments":
        return result.error_message or "I need a little more detail for that live lookup."
    if result.error_code == "provider_unavailable":
        configured = {
            "weather": "I couldn't retrieve the weather right now.",
            "news": "Live news isn't configured yet.",
            "web": "Current web lookup isn't configured yet.",
            "market": "Market quotes aren't configured yet.",
            "crypto": "Crypto quotes aren't configured yet.",
            "sports": "Sports lookup isn't available right now.",
            "places": "Places search isn't configured yet.",
            "time": "Time lookup isn't available right now.",
        }
        return configured.get(category, "That live lookup isn't configured yet.")
    if result.error_code == "provider_timeout":
        return f"I couldn't retrieve {category} information quickly enough."
    if result.error_code == "malformed_result":
        return f"I couldn't verify the {category} data that came back."
    if result.error_code == "unsupported_request":
        return result.error_message or "That live lookup is not supported yet."
    return "I couldn't get trusted live data for that."
