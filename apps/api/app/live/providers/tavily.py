"""Tavily current-web search provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveProviderUnavailable
from app.live.schemas import SourceMetadata, WebReport, WebResult, WebSearchArgs


class TavilyWebSearchProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def search_web(self, args: WebSearchArgs) -> WebReport:
        retrieved_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "query": args.query,
                        "search_depth": "basic",
                        "max_results": args.max_results,
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Web search provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Web search provider failed.") from exc
        return _normalize(args, response.json(), retrieved_at)


def _normalize(
    args: WebSearchArgs, payload: dict[str, Any], retrieved_at: datetime
) -> WebReport:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise LiveMalformedResult("Web search result was malformed.")
    results: list[WebResult] = []
    for item in raw_results:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        results.append(
            WebResult(
                title=item["title"],
                url=item.get("url") if isinstance(item.get("url"), str) else None,
                snippet=item.get("content") if isinstance(item.get("content"), str) else None,
            )
        )
        if len(results) >= args.max_results:
            break
    if not results:
        raise LiveMalformedResult("Web search provider returned no usable results.")
    return WebReport(
        query=args.query,
        results=tuple(results),
        source=SourceMetadata(
            provider="tavily",
            retrieved_at=retrieved_at,
            title="Tavily Search",
            url="https://www.tavily.com/",
            freshness="search response at request time",
        ),
    )
