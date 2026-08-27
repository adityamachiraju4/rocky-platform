"""NewsAPI provider adapter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveProviderUnavailable
from app.live.schemas import NewsArgs, NewsArticle, NewsReport, SourceMetadata


class NewsApiProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def search_news(self, args: NewsArgs) -> NewsReport:
        retrieved_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    "https://newsapi.org/v2/everything",
                    headers={"X-Api-Key": self._api_key},
                    params={
                        "q": args.query,
                        "pageSize": args.max_results,
                        "sortBy": "publishedAt",
                        "from": (retrieved_at - timedelta(days=7)).date().isoformat(),
                        "language": "en",
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("News provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("News provider failed.") from exc
        payload = response.json()
        articles = _articles(payload, args.max_results)
        return NewsReport(
            query=args.query,
            articles=articles,
            source=SourceMetadata(
                provider="newsapi",
                retrieved_at=retrieved_at,
                title="NewsAPI Everything",
                url="https://newsapi.org/",
                freshness="articles requested from the last 7 days",
            ),
        )


def _articles(payload: dict[str, Any], max_results: int) -> tuple[NewsArticle, ...]:
    raw_articles = payload.get("articles")
    if not isinstance(raw_articles, list):
        raise LiveMalformedResult("News result was malformed.")
    seen: set[str] = set()
    articles: list[NewsArticle] = []
    for item in raw_articles:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        normalized_title = title.strip().lower()
        if normalized_title in seen:
            continue
        seen.add(normalized_title)
        source = item.get("source")
        source_name = (
            source.get("name")
            if isinstance(source, dict) and isinstance(source.get("name"), str)
            else "Unknown source"
        )
        articles.append(
            NewsArticle(
                title=title.strip(),
                source_name=source_name,
                url=item.get("url") if isinstance(item.get("url"), str) else None,
                published_at=_parse_datetime(item.get("publishedAt")),
                summary=(
                    item.get("description")
                    if isinstance(item.get("description"), str)
                    else None
                ),
            )
        )
        if len(articles) >= max_results:
            break
    if not articles:
        raise LiveMalformedResult("News provider returned no usable articles.")
    return tuple(articles)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
