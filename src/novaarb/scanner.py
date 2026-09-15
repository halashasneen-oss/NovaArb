from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal

from novaarb.binance import BinanceDepthStream
from novaarb.costs import ExecutableEdgeModel, FeeSchedule
from novaarb.domain import ArbitrageOpportunity, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder
from novaarb.risk import RiskDecision, RiskEngine, RiskLimits
from novaarb.strategies.spot_perp import SpotPerpConfig, SpotPerpStrategy


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    symbols: tuple[str, ...]
    target_notional_usd: Decimal = Decimal("50")
    min_net_edge_bps: Decimal = Decimal("2")
    max_book_age_ms: int = 750
    max_notional_usd: Decimal = Decimal("100")
    spot_taker_fee_bps: Decimal = Decimal("10")
    futures_taker_fee_bps: Decimal = Decimal("5")
    latency_reserve_bps: Decimal = Decimal("0.75")
    emit_cooldown_ms: int = 1000


@dataclass(frozen=True, slots=True)
class ScannerEvent:
    opportunity: ArbitrageOpportunity
    risk: RiskDecision


class SpotPerpScanner:
    def __init__(self, config: ScannerConfig, recorder: ResearchRecorder | None = None) -> None:
        self.config = config
        self.recorder = recorder
        fees = FeeSchedule(config.spot_taker_fee_bps, config.futures_taker_fee_bps)
        model = ExecutableEdgeModel(fees, config.latency_reserve_bps)
        self.strategy = SpotPerpStrategy(
            config=SpotPerpConfig(target_notional_usd=config.target_notional_usd),
            fees=fees,
            edge_model=model,
        )
        self.risk = RiskEngine(
            RiskLimits(
                min_net_edge_bps=config.min_net_edge_bps,
                max_book_age_ms=config.max_book_age_ms,
                max_notional_usd=config.max_notional_usd,
            )
        )
        self.books: dict[tuple[MarketType, str], OrderBookSnapshot] = {}
        self.last_emit_ms: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def events(self) -> asyncio.Queue[ScannerEvent]:
        queue: asyncio.Queue[ScannerEvent] = asyncio.Queue(maxsize=4096)
        raw_queue: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=4096)

        async def pump(stream: BinanceDepthStream) -> None:
            async for snapshot in stream.snapshots():
                if raw_queue.full():
                    _ = raw_queue.get_nowait()
                await raw_queue.put(snapshot)

        spot_stream = BinanceDepthStream(symbols=self.config.symbols, market=MarketType.SPOT)
        perp_stream = BinanceDepthStream(symbols=self.config.symbols, market=MarketType.PERPETUAL)
        for coroutine in (
            pump(spot_stream),
            pump(perp_stream),
            self._evaluate_loop(raw_queue, queue),
        ):
            task = asyncio.create_task(coroutine)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return queue

    async def _evaluate_loop(
        self,
        raw_queue: asyncio.Queue[OrderBookSnapshot],
        event_queue: asyncio.Queue[ScannerEvent],
    ) -> None:
        while True:
            snapshot = await raw_queue.get()
            now_ms = int(time.time() * 1000)
            events = self.process_snapshot(snapshot, now_ms=now_ms)
            for event in events:
                if not event.risk.approved:
                    continue
                last = self.last_emit_ms.get(snapshot.symbol, 0)
                if now_ms - last < self.config.emit_cooldown_ms:
                    continue
                self.last_emit_ms[snapshot.symbol] = now_ms
                await event_queue.put(event)

    def process_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        *,
        now_ms: int | None = None,
    ) -> tuple[ScannerEvent, ...]:
        now_ms = now_ms if now_ms is not None else snapshot.received_time_ms
        if self.recorder is not None:
            self.recorder.append_book(snapshot)
        self.books[(snapshot.market, snapshot.symbol)] = snapshot
        spot = self.books.get((MarketType.SPOT, snapshot.symbol))
        perp = self.books.get((MarketType.PERPETUAL, snapshot.symbol))
        if spot is None or perp is None:
            return ()

        output: list[ScannerEvent] = []
        for opportunity in self.strategy.evaluate(spot, perp, now_ms=now_ms):
            event = ScannerEvent(opportunity, self.risk.assess(opportunity))
            output.append(event)
            if self.recorder is not None:
                self.recorder.append_evaluation(event)
        return tuple(output)
