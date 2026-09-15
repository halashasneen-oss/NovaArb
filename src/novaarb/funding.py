from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.request import Request, urlopen

from novaarb.binance import BinanceDepthStream
from novaarb.domain import TEN_THOUSAND, ZERO, MarketType, OrderBookSnapshot, Side
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill


PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


@dataclass(frozen=True, slots=True)
class FundingSnapshot:
    symbol: str
    funding_rate: Decimal
    next_funding_time_ms: int
    mark_price: Decimal
    index_price: Decimal
    received_time_ms: int

    @property
    def funding_rate_bps(self) -> Decimal:
        return self.funding_rate * TEN_THOUSAND

    def age_ms(self, now_ms: int) -> int:
        return max(0, now_ms - self.received_time_ms)


class FundingCarryReason(StrEnum):
    APPROVED = "approved"
    NON_POSITIVE_FUNDING = "non_positive_funding"
    EDGE_TOO_SMALL = "edge_too_small"
    STALE_BOOK = "stale_book"
    BOOK_SKEW = "book_skew"
    STALE_FUNDING = "stale_funding"


@dataclass(frozen=True, slots=True)
class FundingCarryDecision:
    approved: bool
    reason: FundingCarryReason


@dataclass(frozen=True, slots=True)
class FundingCarryConfig:
    target_notional_usdt: Decimal = Decimal("100")
    funding_intervals: int = 1
    funding_haircut: Decimal = Decimal("0.50")
    spot_taker_fee_bps: Decimal = Decimal("10")
    futures_taker_fee_bps: Decimal = Decimal("5")
    exit_market_reserve_bps: Decimal = Decimal("4")
    basis_risk_reserve_bps: Decimal = Decimal("5")
    min_net_edge_bps: Decimal = Decimal("2")
    max_book_age_ms: int = 750
    max_book_skew_ms: int = 250
    max_funding_age_ms: int = 60_000

    def __post_init__(self) -> None:
        if self.target_notional_usdt <= ZERO:
            raise ValueError("target_notional_usdt must be positive")
        if self.funding_intervals <= 0:
            raise ValueError("funding_intervals must be positive")
        if not ZERO <= self.funding_haircut <= Decimal("1"):
            raise ValueError("funding_haircut must be between 0 and 1")
        if min(
            self.spot_taker_fee_bps,
            self.futures_taker_fee_bps,
            self.exit_market_reserve_bps,
            self.basis_risk_reserve_bps,
            self.min_net_edge_bps,
        ) < ZERO:
            raise ValueError("cost and edge parameters cannot be negative")


@dataclass(frozen=True, slots=True)
class FundingCarryOpportunity:
    symbol: str
    strategy: str
    base_quantity: Decimal
    spot_entry_price: Decimal
    futures_entry_price: Decimal
    reference_notional_usdt: Decimal
    funding_rate_bps: Decimal
    current_basis_bps: Decimal
    expected_funding_usdt: Decimal
    entry_execution_cost_usdt: Decimal
    entry_fees_usdt: Decimal
    exit_reserve_usdt: Decimal
    basis_risk_reserve_usdt: Decimal
    expected_net_profit_usdt: Decimal
    expected_net_edge_bps: Decimal
    created_time_ms: int
    spot_book_age_ms: int
    futures_book_age_ms: int
    funding_age_ms: int

    @property
    def book_skew_ms(self) -> int:
        return abs(self.spot_book_age_ms - self.futures_book_age_ms)


class FundingCarryStrategy:
    """Conservative long-spot/short-perpetual funding-carry research model."""

    name = "funding_carry_long_spot_short_perp"

    def __init__(self, config: FundingCarryConfig) -> None:
        self.config = config

    def evaluate(
        self,
        spot: OrderBookSnapshot,
        perpetual: OrderBookSnapshot,
        funding: FundingSnapshot,
        *,
        now_ms: int,
    ) -> FundingCarryOpportunity | None:
        if spot.market is not MarketType.SPOT or perpetual.market is not MarketType.PERPETUAL:
            raise ValueError("funding carry requires spot and perpetual books")
        if spot.symbol != perpetual.symbol or spot.symbol != funding.symbol:
            raise ValueError("spot, perpetual and funding symbols must match")
        if funding.funding_rate <= ZERO:
            return None

        base_quantity = self.config.target_notional_usdt / spot.mid_price
        try:
            spot_fill = simulate_base_fill(spot, Side.BUY, base_quantity)
            futures_fill = simulate_base_fill(perpetual, Side.SELL, base_quantity)
        except InsufficientLiquidity:
            return None

        reference_notional = (
            spot_fill.quote_quantity + futures_fill.quote_quantity
        ) / Decimal("2")
        expected_funding = (
            futures_fill.quote_quantity
            * funding.funding_rate
            * Decimal(self.config.funding_intervals)
            * self.config.funding_haircut
        )
        spot_execution_cost = max(
            ZERO,
            spot_fill.quote_quantity - base_quantity * spot.mid_price,
        )
        futures_execution_cost = max(
            ZERO,
            base_quantity * perpetual.mid_price - futures_fill.quote_quantity,
        )
        entry_execution_cost = spot_execution_cost + futures_execution_cost
        entry_fees = (
            spot_fill.quote_quantity * self.config.spot_taker_fee_bps / TEN_THOUSAND
            + futures_fill.quote_quantity * self.config.futures_taker_fee_bps / TEN_THOUSAND
        )
        exit_reserve = (
            reference_notional * self.config.exit_market_reserve_bps / TEN_THOUSAND
        )
        basis_risk_reserve = (
            reference_notional * self.config.basis_risk_reserve_bps / TEN_THOUSAND
        )
        expected_net = (
            expected_funding
            - entry_execution_cost
            - entry_fees
            - exit_reserve
            - basis_risk_reserve
        )
        expected_edge = expected_net / reference_notional * TEN_THOUSAND
        basis_bps = (
            (perpetual.mid_price - spot.mid_price) / spot.mid_price * TEN_THOUSAND
        )
        return FundingCarryOpportunity(
            symbol=spot.symbol,
            strategy=self.name,
            base_quantity=base_quantity,
            spot_entry_price=spot_fill.average_price,
            futures_entry_price=futures_fill.average_price,
            reference_notional_usdt=reference_notional,
            funding_rate_bps=funding.funding_rate_bps,
            current_basis_bps=basis_bps,
            expected_funding_usdt=expected_funding,
            entry_execution_cost_usdt=entry_execution_cost,
            entry_fees_usdt=entry_fees,
            exit_reserve_usdt=exit_reserve,
            basis_risk_reserve_usdt=basis_risk_reserve,
            expected_net_profit_usdt=expected_net,
            expected_net_edge_bps=expected_edge,
            created_time_ms=now_ms,
            spot_book_age_ms=spot.age_ms(now_ms),
            futures_book_age_ms=perpetual.age_ms(now_ms),
            funding_age_ms=funding.age_ms(now_ms),
        )

    def assess(self, opportunity: FundingCarryOpportunity) -> FundingCarryDecision:
        if opportunity.funding_rate_bps <= ZERO:
            return FundingCarryDecision(False, FundingCarryReason.NON_POSITIVE_FUNDING)
        if max(opportunity.spot_book_age_ms, opportunity.futures_book_age_ms) > self.config.max_book_age_ms:
            return FundingCarryDecision(False, FundingCarryReason.STALE_BOOK)
        if opportunity.book_skew_ms > self.config.max_book_skew_ms:
            return FundingCarryDecision(False, FundingCarryReason.BOOK_SKEW)
        if opportunity.funding_age_ms > self.config.max_funding_age_ms:
            return FundingCarryDecision(False, FundingCarryReason.STALE_FUNDING)
        if opportunity.expected_net_edge_bps < self.config.min_net_edge_bps:
            return FundingCarryDecision(False, FundingCarryReason.EDGE_TOO_SMALL)
        return FundingCarryDecision(True, FundingCarryReason.APPROVED)


def parse_funding_payload(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    received_time_ms: int,
) -> tuple[FundingSnapshot, ...]:
    rows = payload if isinstance(payload, list) else [payload]
    output: list[FundingSnapshot] = []
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        mark = Decimal(str(row.get("markPrice", "0")))
        index = Decimal(str(row.get("indexPrice", "0")))
        if mark <= ZERO or index <= ZERO:
            continue
        output.append(
            FundingSnapshot(
                symbol=symbol,
                funding_rate=Decimal(str(row.get("lastFundingRate", "0"))),
                next_funding_time_ms=int(row.get("nextFundingTime", 0)),
                mark_price=mark,
                index_price=index,
                received_time_ms=received_time_ms,
            )
        )
    return tuple(output)


def fetch_funding_snapshots(timeout: float = 10.0) -> tuple[FundingSnapshot, ...]:
    received_ms = int(time.time() * 1000)
    request = Request(PREMIUM_INDEX_URL, headers={"User-Agent": "NovaArb/0.5"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Binance HTTPS URL
        payload = json.loads(response.read().decode("utf-8"))
    return parse_funding_payload(payload, received_time_ms=received_ms)


@dataclass(frozen=True, slots=True)
class FundingScannerEvent:
    opportunity: FundingCarryOpportunity
    decision: FundingCarryDecision


class FundingCarryScanner:
    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        config: FundingCarryConfig,
        funding_refresh_seconds: int = 60,
        emit_cooldown_ms: int = 5_000,
    ) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        if funding_refresh_seconds <= 0:
            raise ValueError("funding_refresh_seconds must be positive")
        self.symbols = tuple(symbol.upper() for symbol in symbols)
        self.config = config
        self.strategy = FundingCarryStrategy(config)
        self.funding_refresh_seconds = funding_refresh_seconds
        self.emit_cooldown_ms = emit_cooldown_ms
        self.books: dict[tuple[MarketType, str], OrderBookSnapshot] = {}
        self.funding: dict[str, FundingSnapshot] = {}
        self.last_emit_ms: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def update_funding(self, snapshots: tuple[FundingSnapshot, ...]) -> None:
        allowed = set(self.symbols)
        for snapshot in snapshots:
            if snapshot.symbol in allowed:
                self.funding[snapshot.symbol] = snapshot

    def process_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        *,
        now_ms: int | None = None,
    ) -> FundingScannerEvent | None:
        if snapshot.symbol not in self.symbols:
            return None
        now_ms = snapshot.received_time_ms if now_ms is None else now_ms
        self.books[(snapshot.market, snapshot.symbol)] = snapshot
        spot = self.books.get((MarketType.SPOT, snapshot.symbol))
        perpetual = self.books.get((MarketType.PERPETUAL, snapshot.symbol))
        funding = self.funding.get(snapshot.symbol)
        if spot is None or perpetual is None or funding is None:
            return None
        opportunity = self.strategy.evaluate(spot, perpetual, funding, now_ms=now_ms)
        if opportunity is None:
            return None
        return FundingScannerEvent(opportunity, self.strategy.assess(opportunity))

    async def events(self) -> asyncio.Queue[FundingScannerEvent]:
        output: asyncio.Queue[FundingScannerEvent] = asyncio.Queue(maxsize=4096)
        raw: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=4096)

        async def pump(stream: BinanceDepthStream) -> None:
            async for snapshot in stream.snapshots():
                if raw.full():
                    _ = raw.get_nowait()
                await raw.put(snapshot)

        async def refresh_funding() -> None:
            while True:
                snapshots = await asyncio.to_thread(fetch_funding_snapshots)
                self.update_funding(snapshots)
                await asyncio.sleep(self.funding_refresh_seconds)

        async def evaluate() -> None:
            while True:
                snapshot = await raw.get()
                now_ms = int(time.time() * 1000)
                event = self.process_snapshot(snapshot, now_ms=now_ms)
                if event is None or not event.decision.approved:
                    continue
                last = self.last_emit_ms.get(snapshot.symbol, 0)
                if now_ms - last < self.emit_cooldown_ms:
                    continue
                self.last_emit_ms[snapshot.symbol] = now_ms
                await output.put(event)

        streams = (
            BinanceDepthStream(symbols=self.symbols, market=MarketType.SPOT),
            BinanceDepthStream(symbols=self.symbols, market=MarketType.PERPETUAL),
        )
        for coroutine in (
            pump(streams[0]),
            pump(streams[1]),
            refresh_funding(),
            evaluate(),
        ):
            task = asyncio.create_task(coroutine)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return output
