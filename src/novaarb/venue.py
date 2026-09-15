from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from novaarb.binance import BinanceDepthStream
from novaarb.domain import MarketType, OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class Instrument:
    base_asset: str
    quote_asset: str
    market: MarketType

    def __post_init__(self) -> None:
        if not self.base_asset or not self.quote_asset:
            raise ValueError("base_asset and quote_asset are required")
        object.__setattr__(self, "base_asset", self.base_asset.upper())
        object.__setattr__(self, "quote_asset", self.quote_asset.upper())
        if self.base_asset == self.quote_asset:
            raise ValueError("base_asset and quote_asset must differ")

    @property
    def canonical_symbol(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}:{self.market.value}"


@dataclass(frozen=True, slots=True)
class VenueInstrument:
    venue: str
    venue_symbol: str
    instrument: Instrument

    def __post_init__(self) -> None:
        if not self.venue or not self.venue_symbol:
            raise ValueError("venue and venue_symbol are required")


@dataclass(frozen=True, slots=True)
class NormalizedBook:
    instrument: Instrument
    venue_symbol: str
    snapshot: OrderBookSnapshot

    def __post_init__(self) -> None:
        if self.snapshot.market is not self.instrument.market:
            raise ValueError("snapshot market does not match normalized instrument")
        if self.snapshot.symbol != self.venue_symbol:
            raise ValueError("snapshot symbol does not match venue_symbol")

    @property
    def venue(self) -> str:
        return self.snapshot.venue


class PublicVenueAdapter(Protocol):
    venue: str

    async def books(
        self,
        instruments: tuple[VenueInstrument, ...],
    ) -> AsyncIterator[NormalizedBook]: ...


class BinancePublicVenueAdapter:
    """Normalizes existing Binance depth streams behind the venue protocol."""

    venue = "binance"

    async def books(
        self,
        instruments: tuple[VenueInstrument, ...],
    ) -> AsyncIterator[NormalizedBook]:
        if not instruments:
            raise ValueError("at least one instrument is required")
        if any(item.venue.lower() != self.venue for item in instruments):
            raise ValueError("all instruments must target Binance")

        by_market: dict[MarketType, list[VenueInstrument]] = {}
        by_key: dict[tuple[MarketType, str], VenueInstrument] = {}
        for item in instruments:
            by_market.setdefault(item.instrument.market, []).append(item)
            key = (item.instrument.market, item.venue_symbol.upper())
            if key in by_key:
                raise ValueError(f"duplicate Binance instrument mapping for {key}")
            by_key[key] = item

        import asyncio

        queue: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=4096)
        tasks: set[asyncio.Task[None]] = set()

        async def pump(market: MarketType, items: list[VenueInstrument]) -> None:
            stream = BinanceDepthStream(
                symbols=tuple(item.venue_symbol for item in items),
                market=market,
            )
            async for snapshot in stream.snapshots():
                if queue.full():
                    _ = queue.get_nowait()
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
