from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot


SPOT_WS = "wss://stream.binance.com:9443/stream"
FUTURES_WS = "wss://fstream.binance.com/stream"


class BinanceDepthStream:
    """Public top-20 partial depth stream. No API credentials are used."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        market: MarketType,
        depth: int = 20,
        update_ms: int = 100,
    ) -> None:
        if depth not in {5, 10, 20}:
            raise ValueError("Binance partial depth supports 5, 10 or 20 levels")
        if update_ms not in {100, 1000}:
            raise ValueError("update_ms must be 100 or 1000")
        if not symbols:
            raise ValueError("at least one symbol is required")
        self.symbols = tuple(symbol.upper() for symbol in symbols)
        self.market = market
        self.depth = depth
        self.update_ms = update_ms

    @property
    def url(self) -> str:
        base = SPOT_WS if self.market is MarketType.SPOT else FUTURES_WS
        streams = "/".join(
            f"{symbol.lower()}@depth{self.depth}@{self.update_ms}ms" for symbol in self.symbols
        )
        return f"{base}?streams={streams}"

    async def snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        import websockets

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=2048,
                ) as websocket:
                    backoff = 1.0
                    async for raw in websocket:
                        received_ms = int(time.time() * 1000)
                        payload = json.loads(raw)
                        yield self._parse(payload, received_ms)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _parse(self, payload: dict[str, Any], received_ms: int) -> OrderBookSnapshot:
        data = payload.get("data", payload)
        stream_name = str(payload.get("stream", ""))
        symbol = stream_name.split("@", 1)[0].upper() if stream_name else str(data.get("s", "")).upper()
        if not symbol:
            raise ValueError("cannot determine symbol from Binance depth event")

        raw_bids = data.get("b", data.get("bids"))
        raw_asks = data.get("a", data.get("asks"))
        if not raw_bids or not raw_asks:
            raise ValueError("Binance depth event has no bids/asks")

        bids = tuple(
            BookLevel(Decimal(str(price)), Decimal(str(quantity)))
            for price, quantity, *_ in raw_bids
            if Decimal(str(quantity)) > 0
        )
        asks = tuple(
            BookLevel(Decimal(str(price)), Decimal(str(quantity)))
            for price, quantity, *_ in raw_asks
            if Decimal(str(quantity)) > 0
        )
        bids = tuple(sorted(bids, key=lambda level: level.price, reverse=True))
        asks = tuple(sorted(asks, key=lambda level: level.price))
        event_time_ms = int(data.get("E", received_ms))

        return OrderBookSnapshot(
            venue="binance",
            symbol=symbol,
            market=self.market,
            bids=bids,
            asks=asks,
            event_time_ms=event_time_ms,
            received_time_ms=received_ms,
        )
