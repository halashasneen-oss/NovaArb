from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from novaarb.binance import BinanceDepthStream
from novaarb.domain import MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder
from novaarb.symbols import SymbolRules
from novaarb.triangular import (
    TriangleConfig,
    TrianglePlanner,
    TriangleRiskDecision,
    TriangleRoute,
    TriangularOpportunity,
    TriangularStrategy,
)


@dataclass(frozen=True, slots=True)
class TriangleScannerEvent:
    opportunity: TriangularOpportunity
    risk: TriangleRiskDecision


class TriangularScanner:
    """Incremental triangle scanner: only re-evaluates routes touched by an update."""

    def __init__(
        self,
        *,
        rules: tuple[SymbolRules, ...],
        routes: tuple[TriangleRoute, ...],
        config: TriangleConfig,
        recorder: ResearchRecorder | None = None,
        emit_cooldown_ms: int = 500,
    ) -> None:
        self.rules = rules
        self.routes = routes
        self.config = config
        self.recorder = recorder
        self.emit_cooldown_ms = emit_cooldown_ms
        self.strategy = TriangularStrategy(rules=rules, config=config)
        self.books: dict[str, OrderBookSnapshot] = {}
        self.routes_by_symbol: dict[str, list[TriangleRoute]] = {}
        for route in routes:
            for symbol in route.symbols:
                self.routes_by_symbol.setdefault(symbol, []).append(route)
        self.last_emit_ms: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def plan(
        cls,
        *,
        rules: tuple[SymbolRules, ...],
        anchor_asset: str,
        allowed_assets: set[str],
        config: TriangleConfig,
        recorder: ResearchRecorder | None = None,
    ) -> TriangularScanner:
        planner = TrianglePlanner(rules)
        routes = planner.routes(anchor_asset=anchor_asset, allowed_assets=allowed_assets)
        if not routes:
            raise ValueError("no triangular routes found for requested asset universe")
        used_symbols = {symbol for route in routes for symbol in route.symbols}
        used_rules = tuple(rule for rule in rules if rule.symbol in used_symbols)
        return cls(
            rules=used_rules,
            routes=routes,
            config=config,
            recorder=recorder,
        )

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({symbol for route in self.routes for symbol in route.symbols}))

    def process_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        *,
        now_ms: int | None = None,
    ) -> tuple[TriangleScannerEvent, ...]:
        if snapshot.market is not MarketType.SPOT:
            return ()
        now_ms = now_ms if now_ms is not None else snapshot.received_time_ms
        if self.recorder is not None:
            self.recorder.append_book(snapshot)
        self.books[snapshot.symbol] = snapshot

        output: list[TriangleScannerEvent] = []
        for route in self.routes_by_symbol.get(snapshot.symbol, []):
            opportunity = self.strategy.evaluate_route(route, self.books, now_ms=now_ms)
            if opportunity is None:
                continue
            event = TriangleScannerEvent(opportunity, self.strategy.assess(opportunity))
            output.append(event)
            if self.recorder is not None:
                self.recorder.append_evaluation(event)
        return tuple(output)

    async def events(self) -> asyncio.Queue[TriangleScannerEvent]:
        output: asyncio.Queue[TriangleScannerEvent] = asyncio.Queue(maxsize=4096)
        raw: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=4096)
        stream = BinanceDepthStream(symbols=self.symbols, market=MarketType.SPOT)

        async def pump() -> None:
            async for snapshot in stream.snapshots():
                if raw.full():
                    _ = raw.get_nowait()
                await raw.put(snapshot)

        async def evaluate() -> None:
            while True:
                snapshot = await raw.get()
                now_ms = int(time.time() * 1000)
                for event in self.process_snapshot(snapshot, now_ms=now_ms):
                    if not event.risk.approved:
                        continue
                    route_id = event.opportunity.route.route_id
                    last = self.last_emit_ms.get(route_id, 0)
                    if now_ms - last < self.emit_cooldown_ms:
                        continue
                    self.last_emit_ms[route_id] = now_ms
                    await output.put(event)

        for coroutine in (pump(), evaluate()):
            task = asyncio.create_task(coroutine)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return output
