from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


class MarketType(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.price <= ZERO:
            raise ValueError("book level price must be positive")
        if self.quantity <= ZERO:
            raise ValueError("book level quantity must be positive")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    venue: str
    symbol: str
    market: MarketType
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    event_time_ms: int
    received_time_ms: int

    def __post_init__(self) -> None:
        if not self.bids or not self.asks:
            raise ValueError("order book requires at least one bid and one ask")
        if any(a.price >= b.price for a, b in zip(self.asks, self.asks[1:], strict=False)):
            raise ValueError("asks must be strictly ascending by price")
        if any(a.price <= b.price for a, b in zip(self.bids, self.bids[1:], strict=False)):
            raise ValueError("bids must be strictly descending by price")
        if self.best_bid.price >= self.best_ask.price:
            raise ValueError("crossed/locked order book is not accepted")

    @property
    def best_bid(self) -> BookLevel:
        return self.bids[0]

    @property
    def best_ask(self) -> BookLevel:
        return self.asks[0]

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid.price + self.best_ask.price) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        return (self.best_ask.price - self.best_bid.price) / self.mid_price * TEN_THOUSAND

    def age_ms(self, now_ms: int) -> int:
        return max(0, now_ms - self.received_time_ms)


@dataclass(frozen=True, slots=True)
class BookFill:
    side: Side
    base_quantity: Decimal
    quote_quantity: Decimal
    average_price: Decimal
    top_price: Decimal
    depth_slippage_usd: Decimal
    levels_used: int


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    gross_dislocation_usd: Decimal
    entry_spread_cost_usd: Decimal
    depth_slippage_usd: Decimal
    entry_fees_usd: Decimal
    latency_reserve_usd: Decimal
    exit_reserve_usd: Decimal
    funding_reserve_usd: Decimal
    net_capture_usd: Decimal
    net_edge_bps: Decimal


@dataclass(frozen=True, slots=True)
class ArbitrageOpportunity:
    strategy: str
    venue: str
    symbol: str
    buy_market: MarketType
    sell_market: MarketType
    base_quantity: Decimal
    buy_fill: BookFill
    sell_fill: BookFill
    costs: CostBreakdown
    created_time_ms: int
    buy_book_age_ms: int
    sell_book_age_ms: int

    @property
    def reference_notional_usd(self) -> Decimal:
        return (self.buy_fill.quote_quantity + self.sell_fill.quote_quantity) / Decimal("2")
