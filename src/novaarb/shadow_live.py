from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import median

from novaarb.allocator import AllocationConfig, CapitalAwareAllocator, ResearchCandidate
from novaarb.cross_venue import CrossVenueOpportunity, VenueCostProfile
from novaarb.domain import MarketType, TEN_THOUSAND, ZERO
from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.research import ResearchRecorder
from novaarb.shadow import (
    MultiAssetInventoryLedger,
    PortfolioMark,
    ShadowKillSwitchReason,
    ShadowRiskConfig,
    ShadowRiskGuard,
)
from novaarb.shadow_scanner import (
    MultiInstrumentPublicShadowScanner,
    MultiInstrumentShadowEngine,
    ShadowScannerEvent,
)


@dataclass(frozen=True, slots=True)
class ShadowLiveConfig:
    allocation_window_ms: int = 100
    heartbeat_interval_ms: int = 5_000
    max_data_staleness_ms: int = 2_000

    def __post_init__(self) -> None:
        if self.allocation_window_ms < 0:
            raise ValueError("allocation_window_ms cannot be negative")
        if self.heartbeat_interval_ms <= 0:
            raise ValueError("heartbeat_interval_ms must be positive")
        if self.max_data_staleness_ms < 0:
            raise ValueError("max_data_staleness_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class ShadowLiveTrade:
    timestamp_ms: int
    instrument: str
    buy_venue: str
    sell_venue: str
    detected_edge_bps: Decimal
    realized_edge_bps: Decimal
    conservative_net_profit_quote: Decimal


@dataclass(frozen=True, slots=True)
class ShadowLiveBatchResult:
    observed: int
    selected: int
    executed: int
    risk_halts: int
    decayed: int
    inventory_rejections: int
    trades: tuple[ShadowLiveTrade, ...]


class ShadowLiveCoordinator:
    """Applies allocation, risk and hypothetical fills to public shadow signals only."""

    def __init__(
        self,
        *,
        engine: MultiInstrumentShadowEngine,
        costs: tuple[VenueCostProfile, ...],
        quote_asset: str,
        allocation_config: AllocationConfig,
        risk_config: ShadowRiskConfig | None = None,
        live_config: ShadowLiveConfig | None = None,
    ) -> None:
        self.engine = engine
        self.inventory: MultiAssetInventoryLedger = engine.inventory
        self.quote_asset = quote_asset.upper()
        self.costs = {item.venue: item for item in costs}
        if len(self.costs) != len(costs) or len(self.costs) < 2:
            raise ValueError("live shadow costs require at least two unique venues")
        if set(self.costs) != set(engine.cost_venues):
            raise ValueError("live shadow costs must match engine venue costs")
        self.allocator = CapitalAwareAllocator(allocation_config)
        self.guard = ShadowRiskGuard(risk_config or ShadowRiskConfig())
        self.config = live_config or ShadowLiveConfig()
        self.sequence = 0
        self.started_ms = int(time.time() * 1000)
        self.detected_signals = 0
        self.allocator_selected = 0
        self.allocator_rejections = 0
        self.decayed_before_execution = 0
        self.inventory_rejections = 0
        self.risk_halts = 0
        self.executed_trades = 0
        self.profitable_trades = 0
        self.conservative_net_profit_quote = ZERO
        self.allocation_reasons: Counter[str] = Counter()
        self.kill_reasons: Counter[str] = Counter()
        self.trades: list[ShadowLiveTrade] = []

    def _prices(self) -> dict[str, Decimal]:
        grouped: dict[str, list[Decimal]] = {}
        for book in self.engine.books.values():
            instrument = book.instrument
            if instrument.market is not MarketType.SPOT:
                continue
            if instrument.quote_asset != self.quote_asset:
                continue
            grouped.setdefault(instrument.base_asset, []).append(book.snapshot.mid_price)
        return {
            asset: Decimal(median(values))
            for asset, values in grouped.items()
            if values
        }

    def portfolio_mark(self) -> PortfolioMark | None:
        prices = self._prices()
        balances = self.inventory.snapshot()
        required = {
            item.asset
            for item in balances
            if item.asset != self.quote_asset and item.quantity > ZERO
        }
        if not required <= set(prices):
            return None
        return self.inventory.mark_to_quote(
            quote_asset=self.quote_asset,
            prices_in_quote=prices,
        )

    def data_healthy(self, *, now_ms: int) -> bool:
        latest: dict[str, int] = {}
        for book in self.engine.books.values():
            latest[book.venue] = max(
                latest.get(book.venue, 0),
                book.snapshot.received_time_ms,
            )
        return all(
            venue in latest
            and now_ms - latest[venue] <= self.config.max_data_staleness_ms
            for venue in self.engine.cost_venues
        )

    def _candidate(self, event: ShadowScannerEvent) -> ResearchCandidate:
        self.sequence += 1
        opportunity = event.opportunity
        instrument = opportunity.instrument
        return ResearchCandidate(
            opportunity_id=(
                f"live:{opportunity.created_time_ms}:{self.sequence}:"
                f"{instrument.canonical_symbol}:{opportunity.buy_venue}:"
                f"{opportunity.sell_venue}"
            ),
            strategy="prefunded_cross_venue",
            capital_required_usdt=(
                opportunity.buy_quote_required + opportunity.sell_quote_proceeds
            ),
            expected_net_profit_usdt=opportunity.net_profit_quote,
            expected_edge_bps=opportunity.net_edge_bps,
            resource_keys=(
                f"instrument:{instrument.canonical_symbol}",
                f"route:{opportunity.buy_venue}:{opportunity.sell_venue}:"
                f"{instrument.canonical_symbol}",
            ),
            observed_at_ms=opportunity.created_time_ms,
        )

    def _fee(self, opportunity: CrossVenueOpportunity, venue: str) -> Decimal:
        profile = self.costs[venue]
        notional = (
            opportunity.buy_quote_required
            if venue == opportunity.buy_venue
            else opportunity.sell_quote_proceeds
        )
        return notional * profile.taker_fee_bps / TEN_THOUSAND

    def process_batch(
        self,
        events: tuple[ShadowScannerEvent, ...],
        *,
        now_ms: int | None = None,
    ) -> ShadowLiveBatchResult:
        if not events:
            return ShadowLiveBatchResult(0, 0, 0, 0, 0, 0, ())
        timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
        self.detected_signals += len(events)
        mark = self.portfolio_mark()
        if mark is None:
            self.risk_halts += len(events)
            self.kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += len(events)
            return ShadowLiveBatchResult(len(events), 0, 0, len(events), 0, 0, ())

        risk = self.guard.assess(
            timestamp_ms=timestamp_ms,
            mark=mark,
            data_healthy=self.data_healthy(now_ms=timestamp_ms),
        )
        if not risk.allowed:
            self.risk_halts += len(events)
            self.kill_reasons[risk.reason.value] += len(events)
            return ShadowLiveBatchResult(len(events), 0, 0, len(events), 0, 0, ())

        candidates = tuple(self._candidate(event) for event in events)
        allocation = self.allocator.allocate(candidates)
        by_id = {
            candidate.opportunity_id: event
            for candidate, event in zip(candidates, events, strict=True)
        }
        for decision in allocation.decisions:
            if decision.selected:
                self.allocator_selected += 1
            else:
                self.allocator_rejections += 1
                self.allocation_reasons[decision.reason.value] += 1

        selected = 0
        decayed = 0
        inventory_rejections = 0
        batch_halts = 0
        completed: list[ShadowLiveTrade] = []
        for candidate in allocation.selected:
            selected += 1
            detected = by_id[candidate.opportunity_id].opportunity
            instrument = detected.instrument
            buy_book = self.engine.books.get(
                (instrument.canonical_symbol, detected.buy_venue)
            )
            sell_book = self.engine.books.get(
                (instrument.canonical_symbol, detected.sell_venue)
            )
            if buy_book is None or sell_book is None:
                self.decayed_before_execution += 1
                decayed += 1
                continue

            realized = self.engine.strategy.evaluate_direction(
                buy_book=buy_book,
                sell_book=sell_book,
                now_ms=timestamp_ms,
            )
            if realized is None:
                self.decayed_before_execution += 1
                decayed += 1
                continue
            inventories = self.engine._instrument_inventories(buy_book)
            approval = self.engine.strategy.assess(realized, inventories=inventories)
            if not approval.approved:
                self.decayed_before_execution += 1
                decayed += 1
                continue

            current_mark = self.portfolio_mark()
            if current_mark is None:
                self.risk_halts += 1
                batch_halts += 1
                self.kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += 1
                continue
            current_risk = self.guard.assess(
                timestamp_ms=timestamp_ms,
                mark=current_mark,
                data_healthy=self.data_healthy(now_ms=timestamp_ms),
            )
            if not current_risk.allowed:
                self.risk_halts += 1
                batch_halts += 1
                self.kill_reasons[current_risk.reason.value] += 1
                continue

            trade = ExecutedCrossVenueTrade(
                buy_venue=realized.buy_venue,
                sell_venue=realized.sell_venue,
                base_quantity=realized.base_quantity,
                buy_quote_spent=realized.buy_quote_required,
                sell_quote_received=realized.sell_quote_proceeds,
                buy_fee_quote=self._fee(realized, realized.buy_venue),
                sell_fee_quote=self._fee(realized, realized.sell_venue),
            )
            try:
                self.engine.apply_shadow_fill(
                    trade,
                    base_asset=instrument.base_asset,
                    quote_asset=instrument.quote_asset,
                )
            except ValueError:
                self.inventory_rejections += 1
                inventory_rejections += 1
                continue

            conservative_profit = realized.net_profit_quote
            self.guard.record_realized_pnl(
                timestamp_ms=timestamp_ms,
                pnl_quote=conservative_profit,
            )
            self.executed_trades += 1
            if conservative_profit > ZERO:
                self.profitable_trades += 1
            self.conservative_net_profit_quote += conservative_profit
            output = ShadowLiveTrade(
                timestamp_ms=timestamp_ms,
                instrument=instrument.canonical_symbol,
                buy_venue=realized.buy_venue,
                sell_venue=realized.sell_venue,
                detected_edge_bps=detected.net_edge_bps,
                realized_edge_bps=realized.net_edge_bps,
                conservative_net_profit_quote=conservative_profit,
            )
            self.trades.append(output)
            completed.append(output)

        return ShadowLiveBatchResult(
            observed=len(events),
            selected=selected,
            executed=len(completed),
            risk_halts=batch_halts,
            decayed=decayed,
            inventory_rejections=inventory_rejections,
            trades=tuple(completed),
        )

    def metrics(self, *, now_ms: int | None = None) -> dict[str, object]:
        timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
        mark = self.portfolio_mark()
        data_healthy = self.data_healthy(now_ms=timestamp_ms)
        risk_reason = ShadowKillSwitchReason.NONE.value
        if mark is None:
            risk_reason = ShadowKillSwitchReason.DATA_UNHEALTHY.value
        else:
            risk_reason = self.guard.assess(
                timestamp_ms=timestamp_ms,
                mark=mark,
                data_healthy=data_healthy,
            ).reason.value
        return {
            "timestamp_ms": timestamp_ms,
            "uptime_ms": max(0, timestamp_ms - self.started_ms),
            "mode": "shadow_public_data_only",
            "quote_asset": self.quote_asset,
            "data_healthy": data_healthy,
            "risk_state": risk_reason,
            "detected_signals": self.detected_signals,
            "allocator_selected": self.allocator_selected,
            "allocator_rejections": self.allocator_rejections,
            "decayed_before_execution": self.decayed_before_execution,
            "inventory_rejections": self.inventory_rejections,
            "risk_halts": self.risk_halts,
            "executed_trades": self.executed_trades,
            "profitable_trades": self.profitable_trades,
            "conservative_net_profit_quote": self.conservative_net_profit_quote,
            "allocation_rejection_reasons": dict(sorted(self.allocation_reasons.items())),
            "kill_switch_reasons": dict(sorted(self.kill_reasons.items())),
            "portfolio_mark": asdict(mark) if mark is not None else None,
            "balances": [asdict(item) for item in self.inventory.snapshot()],
        }


async def run_public_shadow_session(
    *,
    scanner: MultiInstrumentPublicShadowScanner,
    coordinator: ShadowLiveCoordinator,
    metrics_recorder: ResearchRecorder | None = None,
    duration_seconds: float | None = None,
) -> None:
    """Run foreground shadow operation; this function has no order or transfer client."""

    if duration_seconds is not None and duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive when provided")
    queue = await scanner.events()
    loop = asyncio.get_running_loop()
    started = loop.time()
    next_heartbeat = started

    while True:
        now = loop.time()
        if duration_seconds is not None and now - started >= duration_seconds:
            break
        heartbeat_timeout = max(0.0, next_heartbeat - now)
        duration_timeout = (
            max(0.0, duration_seconds - (now - started))
            if duration_seconds is not None
            else None
        )
        timeout = heartbeat_timeout
        if duration_timeout is not None:
            timeout = min(timeout, duration_timeout)

        if timeout <= 0:
            if metrics_recorder is not None:
                metrics_recorder.append_metadata("shadow_heartbeat", coordinator.metrics())
            next_heartbeat = loop.time() + coordinator.config.heartbeat_interval_ms / 1000
            continue

        try:
            first = await asyncio.wait_for(queue.get(), timeout=timeout)
        except TimeoutError:
            continue

        batch = [first]
        deadline = loop.time() + coordinator.config.allocation_window_ms / 1000
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
            except TimeoutError:
                break
        coordinator.process_batch(tuple(batch))
        if metrics_recorder is not None and loop.time() >= next_heartbeat:
            metrics_recorder.append_metadata("shadow_heartbeat", coordinator.metrics())
            next_heartbeat = loop.time() + coordinator.config.heartbeat_interval_ms / 1000

    if metrics_recorder is not None:
        metrics_recorder.append_metadata("shadow_final", coordinator.metrics())
