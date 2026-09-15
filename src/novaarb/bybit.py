from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot, ZERO
from novaarb.stream_telemetry import (
    StreamTelemetry,
    looks_rate_limited,
    payload_looks_rate_limited,
)
from novaarb.venue import NormalizedBook, VenueInstrument


BYBIT_SPOT_WS = "wss://stream.bybit.com/v5/public/spot"
BYBIT_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"


class BybitOrderBookState:
    """Maintains a local Bybit snapshot/delta order book for one symbol."""

    def __init__(
        self,
        *,
        symbol: str,
        market: MarketType,
        depth: int = 50,
    ) -> None:
        if depth <= 0:
            raise ValueError("depth must be positive")
        self.symbol = symbol.upper()
        self.market = market
        self.depth = depth
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}

    def apply(
        self,
        payload: dict[str, Any],
        *,
        received_time_ms: int,
    ) -> OrderBookSnapshot | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        symbol = str(data.get("s", "")).upper()
        if symbol != self.symbol:
            return None

        message_type = str(payload.get("type", ""))
        update_id = int(data.get("u", 0))
        if message_type == "snapshot" or update_id == 1:
            self.bids.clear()
            self.asks.clear()
        elif message_type != "delta":
            return None

        self._apply_side(self.bids, data.get("b", ()))
        self._apply_side(self.asks, data.get("a", ()))
        if not self.bids or not self.asks:
            return None

        bids = tuple(
            BookLevel(price, quantity)
            for price, quantity in sorted(
                self.bids.items(),
                key=lambda item: item[0],
                reverse=True,
            )[: self.depth]
        )
        asks = tuple(
            BookLevel(price, quantity)
            for price, quantity in sorted(
                self.asks.items(),
                key=lambda item: item[0],
            )[: self.depth]
        )
        if bids[0].price >= asks[0].price:
            return None

        return OrderBookSnapshot(
            venue="bybit",
            symbol=self.symbol,
            market=self.market,
            bids=bids,
            asks=asks,
            event_time_ms=int(payload.get("ts", received_time_ms)),
            received_time_ms=received_time_ms,
        )

    @staticmethod
    def _apply_side(
        side: dict[Decimal, Decimal],
        updates: object,
    ) -> None:
        if not isinstance(updates, list | tuple):
            return
        for row in updates:
            if not isinstance(row, list | tuple) or len(row) < 2:
                continue
            price = Decimal(str(row[0]))
            quantity = Decimal(str(row[1]))
            if price <= ZERO or quantity < ZERO:
                continue
            if quantity == ZERO:
                side.pop(price, None)
            else:
                side[price] = quantity


class BybitDepthStream:
    """Public Bybit V5 level-50 order book stream. No credentials are used."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        market: MarketType,
        depth: int = 50,
        telemetry: StreamTelemetry | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        if depth not in {1, 50, 200, 1000}:
            raise ValueError("Bybit depth must be 1, 50, 200 or 1000")
        self.symbols = tuple(symbol.upper() for symbol in symbols)
        self.market = market
        self.depth = depth
        self.telemetry = telemetry

    @property
    def url(self) -> str:
        return BYBIT_SPOT_WS if self.market is MarketType.SPOT else BYBIT_LINEAR_WS

    async def snapshots(self) -> AsyncIterator[OrderBookSnapshot]:
        import websockets

        states = {
            symbol: BybitOrderBookState(
                symbol=symbol,
                market=self.market,
                depth=self.depth,
            )
            for symbol in self.symbols
        }
        topics = [f"orderbook.{self.depth}.{symbol}" for symbol in self.symbols]
        backoff = 1.0

        while True:
            if self.telemetry is not None:
                self.telemetry.record_connection_attempt("bybit", self.market)
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=2048,
                ) as websocket:
                    if self.telemetry is not None:
                        self.telemetry.record_connected(
                            "bybit",
                            self.market,
                            timestamp_ms=int(time.time() * 1000),
                        )
                    await websocket.send(json.dumps({"op": "subscribe", "args": topics}))
                    backoff = 1.0
                    async for raw in websocket:
                        received_ms = int(time.time() * 1000)
                        if self.telemetry is not None:
                            self.telemetry.record_message(
                                "bybit",
                                self.market,
                                timestamp_ms=received_ms,
                            )
                        try:
                            payload = json.loads(raw)
                        except Exception:
                            if self.telemetry is not None:
                                self.telemetry.record_parse_error("bybit", self.market)
                            raise
                        if self.telemetry is not None and payload_looks_rate_limited(payload):
                            self.telemetry.record_rate_limit("bybit", self.market)
                        data = payload.get("data")
                        if not isinstance(data, dict):
                            continue
                        symbol = str(data.get("s", "")).upper()
                        state = states.get(symbol)
                        if state is None:
                            continue
                        try:
                            snapshot = state.apply(
                                payload,
                                received_time_ms=received_ms,
                            )
                        except Exception:
                            if self.telemetry is not None:
                                self.telemetry.record_parse_error("bybit", self.market)
                            raise
                        if snapshot is not None:
                            if self.telemetry is not None:
                                self.telemetry.record_snapshot(
                                    "bybit",
                                    self.market,
                                    timestamp_ms=received_ms,
                                )
                            yield snapshot
                    if self.telemetry is not None:
                        self.telemetry.record_disconnect(
                            "bybit",
                            self.market,
                            timestamp_ms=int(time.time() * 1000),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.telemetry is not None:
                    self.telemetry.record_disconnect(
                        "bybit",
                        self.market,
                        timestamp_ms=int(time.time() * 1000),
                    )
                    if looks_rate_limited(exc):
                        self.telemetry.record_rate_limit("bybit", self.market)
                    self.telemetry.record_backoff(
                        "bybit",
                        self.market,
                        milliseconds=int(backoff * 1000),
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


class BybitPublicVenueAdapter:
    """Normalizes Bybit public books behind the generic venue adapter contract."""

    venue = "bybit"

    def __init__(self, *, telemetry: StreamTelemetry | None = None) -> None:
        self.telemetry = telemetry

    async def books(
        self,
        instruments: tuple[VenueInstrument, ...],
    ) -> AsyncIterator[NormalizedBook]:
        if not instruments:
            raise ValueError("at least one instrument is required")
        if any(item.venue.lower() != self.venue for item in instruments):
            raise ValueError("all instruments must target Bybit")

        by_market: dict[MarketType, list[VenueInstrument]] = {}
        by_key: dict[tuple[MarketType, str], VenueInstrument] = {}
        for item in instruments:
            by_market.setdefault(item.instrument.market, []).append(item)
            key = (item.instrument.market, item.venue_symbol.upper())
            if key in by_key:
                raise ValueError(f"duplicate Bybit instrument mapping for {key}")
            by_key[key] = item

        queue: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=4096)
        tasks: set[asyncio.Task[None]] = set()

        async def pump(market: MarketType, items: list[VenueInstrument]) -> None:
            stream = BybitDepthStream(
                symbols=tuple(item.venue_symbol for item in items),
                market=market,
                telemetry=self.telemetry,
            )
            async for snapshot in stream.snapshots():
                if queue.full():
                    _ = queue.get_nowait()
                    if self.telemetry is not None:
                        self.telemetry.record_queue_drop(self.venue, market)
                await queue.put(snapshot)

        try:
            for market, items in by_market.items():
                task = asyncio.create_task(pump(market, items))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            while True:
                snapshot = await queue.get()
                mapping = by_key.get((snapshot.market, snapshot.symbol.upper()))
                if mapping is None:
                    continue
                yield NormalizedBook(
                    instrument=mapping.instrument,
                    venue_symbol=mapping.venue_symbol.upper(),
                    snapshot=snapshot,
                )
        finally:
            for task in tuple(tasks):
                task.cancel()
