from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from novaarb.cross_venue import (
    CrossVenueConfig,
    CrossVenueDecision,
    CrossVenueOpportunity,
    CrossVenueStrategy,
    VenueCostProfile,
    VenueInventory,
)
from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.research import ResearchRecorder
from novaarb.shadow import MultiAssetInventoryLedger
from novaarb.venue import NormalizedBook, PublicVenueAdapter, VenueInstrument


@dataclass(frozen=True, slots=True)
class ShadowScannerEvent:
    opportunity: CrossVenueOpportunity
    decision: CrossVenueDecision


class MultiInstrumentShadowEngine:
    """Cross-venue research engine backed by shared venue/asset inventory."""

    def __init__(
        self,
        *,
        config: CrossVenueConfig,
        costs: tuple[VenueCostProfile, ...],
        inventory: MultiAssetInventoryLedger,
        recorder: ResearchRecorder | None = None,
    ) -> None:
        if len(costs) < 2:
            raise ValueError("multi-instrument shadow research requires at least two venues")
        self.strategy = CrossVenueStrategy(config=config, costs=costs)
        self.cost_venues = tuple(sorted(profile.venue for profile in costs))
        self.inventory = inventory
        self.recorder = recorder
        self.books: dict[tuple[str, str], NormalizedBook] = {}

    def _instrument_inventories(self, book: NormalizedBook) -> tuple[VenueInventory, ...]:
        instrument = book.instrument
        return tuple(
            VenueInventory(
                venue=venue,
                base_available=self.inventory.balance(venue, instrument.base_asset),
                quote_available=self.inventory.balance(venue, instrument.quote_asset),
            )
            for venue in self.cost_venues
        )

    def process_book(
        self,
        book: NormalizedBook,
        *,
        now_ms: int | None = None,
    ) -> tuple[ShadowScannerEvent, ...]:
        timestamp_ms = book.snapshot.received_time_ms if now_ms is None else now_ms
        if book.venue not in self.cost_venues:
            raise ValueError(f"missing cost profile for venue {book.venue}")
        if self.recorder is not None:
            self.recorder.append_book(book.snapshot)

        instrument_key = book.instrument.canonical_symbol
        self.books[(instrument_key, book.venue)] = book
        inventories = self._instrument_inventories(book)
        events: list[ShadowScannerEvent] = []

        for (candidate_key, venue), candidate in tuple(self.books.items()):
            if candidate_key != instrument_key or venue == book.venue:
                continue
            result = self.strategy.best_direction(
                book,
                candidate,
                now_ms=timestamp_ms,
                inventories=inventories,
            )
            if result is None:
                continue
            opportunity, decision = result
            event = ShadowScannerEvent(opportunity=opportunity, decision=decision)
            events.append(event)
            if self.recorder is not None:
                self.recorder.append_evaluation(event)

        events.sort(key=lambda item: item.opportunity.net_edge_bps, reverse=True)
        return tuple(events)

    def apply_shadow_fill(
        self,
        trade: ExecutedCrossVenueTrade,
        *,
        base_asset: str,
        quote_asset: str,
    ) -> None:
        self.inventory.apply_cross_venue_trade(
            trade,
            base_asset=base_asset,
            quote_asset=quote_asset,
        )


class MultiInstrumentPublicShadowScanner:
    """Runs public venue feeds for many instruments with no authenticated trading client."""

    def __init__(
        self,
        *,
        engine: MultiInstrumentShadowEngine,
        adapters: tuple[PublicVenueAdapter, ...],
        instruments: tuple[VenueInstrument, ...],
        emit_cooldown_ms: int = 1_000,
    ) -> None:
        if len(adapters) < 2:
            raise ValueError("at least two public venue adapters are required")
        adapter_venues = {adapter.venue for adapter in adapters}
        if len(adapter_venues) != len(adapters):
            raise ValueError("public venue adapters must be unique")
        if not instruments:
            raise ValueError("at least one venue instrument is required")
        if emit_cooldown_ms < 0:
            raise ValueError("emit_cooldown_ms cannot be negative")

        venue_counts: dict[str, set[str]] = {}
        for item in instruments:
            venue = item.venue.lower()
            if venue not in adapter_venues:
                raise ValueError("every venue instrument requires a matching adapter")
            venue_counts.setdefault(item.instrument.canonical_symbol, set()).add(venue)
        if any(len(venues) < 2 for venues in venue_counts.values()):
            raise ValueError("every canonical instrument must map to at least two venues")

        self.engine = engine
        self.adapters = adapters
        self.instruments = instruments
        self.emit_cooldown_ms = emit_cooldown_ms
        self.last_emit_ms: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def events(self) -> asyncio.Queue[ShadowScannerEvent]:
        output: asyncio.Queue[ShadowScannerEvent] = asyncio.Queue(maxsize=4096)
        raw: asyncio.Queue[NormalizedBook] = asyncio.Queue(maxsize=4096)
        by_venue: dict[str, list[VenueInstrument]] = {}
        for item in self.instruments:
            by_venue.setdefault(item.venue.lower(), []).append(item)

        async def pump(
            adapter: PublicVenueAdapter,
            items: tuple[VenueInstrument, ...],
        ) -> None:
            async for book in adapter.books(items):
                if raw.full():
                    _ = raw.get_nowait()
                await raw.put(book)

        async def evaluate() -> None:
            while True:
                book = await raw.get()
                now_ms = int(time.time() * 1000)
                for event in self.engine.process_book(book, now_ms=now_ms):
                    if not event.decision.approved:
                        continue
                    opportunity = event.opportunity
                    key = (
                        f"{opportunity.instrument.canonical_symbol}|"
                        f"{opportunity.buy_venue}|{opportunity.sell_venue}"
                    )
                    last = self.last_emit_ms.get(key, 0)
                    if now_ms - last < self.emit_cooldown_ms:
                        continue
                    self.last_emit_ms[key] = now_ms
                    await output.put(event)

        for adapter in self.adapters:
            items = tuple(by_venue.get(adapter.venue, ()))
            if not items:
                continue
            task = asyncio.create_task(pump(adapter, items))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        task = asyncio.create_task(evaluate())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return output
