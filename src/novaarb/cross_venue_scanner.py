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
from novaarb.research import ResearchRecorder
from novaarb.venue import NormalizedBook, PublicVenueAdapter, VenueInstrument


@dataclass(frozen=True, slots=True)
class CrossVenueScannerEvent:
    opportunity: CrossVenueOpportunity
    decision: CrossVenueDecision


class CrossVenueResearchEngine:
    """Synchronously evaluates normalized public books across venues."""

    def __init__(
        self,
        *,
        config: CrossVenueConfig,
        costs: tuple[VenueCostProfile, ...],
        inventories: tuple[VenueInventory, ...],
        recorder: ResearchRecorder | None = None,
    ) -> None:
        if len(inventories) < 2:
            raise ValueError("cross-venue research requires at least two inventories")
        if len({item.venue for item in inventories}) != len(inventories):
            raise ValueError("venue inventories must be unique")
        self.strategy = CrossVenueStrategy(config=config, costs=costs)
        self.inventories = inventories
        self.recorder = recorder
        self.books: dict[tuple[str, str], NormalizedBook] = {}

    def process_book(
        self,
        book: NormalizedBook,
        *,
        now_ms: int | None = None,
    ) -> tuple[CrossVenueScannerEvent, ...]:
        timestamp_ms = book.snapshot.received_time_ms if now_ms is None else now_ms
        if self.recorder is not None:
            self.recorder.append_book(book.snapshot)

        instrument_key = book.instrument.canonical_symbol
        self.books[(instrument_key, book.venue)] = book
        events: list[CrossVenueScannerEvent] = []

        for (candidate_key, venue), candidate in self.books.items():
            if candidate_key != instrument_key or venue == book.venue:
                continue
            result = self.strategy.best_direction(
                book,
                candidate,
                now_ms=timestamp_ms,
                inventories=self.inventories,
            )
            if result is None:
                continue
            opportunity, decision = result
            event = CrossVenueScannerEvent(opportunity, decision)
            events.append(event)
            if self.recorder is not None:
                self.recorder.append_evaluation(event)

        events.sort(
            key=lambda item: item.opportunity.net_edge_bps,
            reverse=True,
        )
        return tuple(events)


class CrossVenuePublicScanner:
    """Runs normalized public venue adapters and emits approved opportunities."""

    def __init__(
        self,
        *,
        engine: CrossVenueResearchEngine,
        adapters: tuple[PublicVenueAdapter, ...],
        instruments: tuple[VenueInstrument, ...],
        emit_cooldown_ms: int = 1_000,
    ) -> None:
        if len(adapters) < 2:
            raise ValueError("at least two public venue adapters are required")
        if len({adapter.venue for adapter in adapters}) != len(adapters):
            raise ValueError("public venue adapters must be unique")
        if not instruments:
            raise ValueError("at least one venue instrument is required")
        if emit_cooldown_ms < 0:
            raise ValueError("emit_cooldown_ms cannot be negative")

        canonical = {item.instrument.canonical_symbol for item in instruments}
        if len(canonical) != 1:
            raise ValueError("current cross-venue scanner supports one instrument at a time")

        adapter_venues = {adapter.venue for adapter in adapters}
        instrument_venues = {item.venue.lower() for item in instruments}
        if not instrument_venues <= adapter_venues:
            raise ValueError("every venue instrument requires a matching adapter")
        if len(instrument_venues) < 2:
            raise ValueError("the instrument must be mapped to at least two venues")

        inventory_venues = {item.venue for item in engine.inventories}
        if instrument_venues != inventory_venues:
            raise ValueError("inventory venues must match instrument venues")

        self.engine = engine
        self.adapters = adapters
        self.instruments = instruments
        self.emit_cooldown_ms = emit_cooldown_ms
        self.last_emit_ms: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def events(self) -> asyncio.Queue[CrossVenueScannerEvent]:
        output: asyncio.Queue[CrossVenueScannerEvent] = asyncio.Queue(maxsize=4096)
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
