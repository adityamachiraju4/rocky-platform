"""Finnhub market quote provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.live.errors import LiveMalformedResult, LiveProviderTimeout, LiveProviderUnavailable
from app.live.schemas import MarketArgs, MarketQuote, SourceMetadata


class FinnhubMarketProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))

    async def quote(self, args: MarketArgs) -> MarketQuote:
        retrieved_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    "https://finnhub.io/api/v1/quote",
                    headers={"X-Finnhub-Token": self._api_key},
                    params={"symbol": args.symbol},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LiveProviderTimeout("Market provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise LiveProviderUnavailable("Market provider failed.") from exc
        return _quote(args, response.json(), retrieved_at)


def _quote(args: MarketArgs, payload: dict[str, Any], retrieved_at: datetime) -> MarketQuote:
    current = payload.get("c")
    if not isinstance(current, (float, int)) or float(current) <= 0:
        raise LiveMalformedResult("Market quote result was malformed.")
    market_time = None
    raw_time = payload.get("t")
    if isinstance(raw_time, (float, int)) and raw_time > 0:
        market_time = datetime.fromtimestamp(float(raw_time), tz=timezone.utc)
    return MarketQuote(
        symbol=args.symbol,
        asset_type=args.asset_type,
        price=float(current),
        currency="USD",
        change=float(payload["d"]) if isinstance(payload.get("d"), (float, int)) else None,
        percent_change=(
            float(payload["dp"]) if isinstance(payload.get("dp"), (float, int)) else None
        ),
        market_time=market_time,
        delayed=True,
        source=SourceMetadata(
            provider="finnhub",
            retrieved_at=retrieved_at,
            title="Finnhub Quote",
            url="https://finnhub.io/",
            freshness="provider quote response; may be delayed by plan",
        ),
    )
