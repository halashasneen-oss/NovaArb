from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import TEN_THOUSAND, ZERO, Side
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill
from novaarb.venue import Instrument, NormalizedBook


@dataclass(frozen=True, slots=True)
class VenueCostProfile:
    venue: str
    taker_fee_bps: Decimal
    execution_reserve_bps: Decimal = Decimal("0")
    rebalance_reserve_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue is required")
        if min(
            self.taker_fee_bps,
            self.execution_reserve_bps,
            self.rebalance_reserve_bps,
        ) < ZERO:
            raise ValueError("venue cost assumptions cannot be negative")


@dataclass(frozen=True, slots=True)
class VenueInventory:
    venue: str
    base_available: Decimal
    quote_available: Decimal

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue is required")
        if self.base_available < ZERO or self.quote_available < ZERO:
            raise ValueError("inventory cannot be negative")


@dataclass(frozen=True, slots=True)
class CrossVenueConfig:
    target_notional_quote: Decimal = Decimal("100")
    min_net_edge_bps: Decimal = Decimal("2")
    max_book_age_ms: int = 500
    max_book_skew_ms: int = 150

    def __post_init__(self) -> None:
        if self.target_notional_quote <= ZERO:
            raise ValueError("target_notional_quote must be positive")
        if self.min_net_edge_bps < ZERO:
            raise ValueError("min_net_edge_bps cannot be negative")
        if self.max_book_age_ms < 0 or self.max_book_skew_ms < 0:
            raise ValueError("book timing limits cannot be negative")


class CrossVenueReason(StrEnum):
    APPROVED = "approved"
    SAME_VENUE = "same_venue"
    INSTRUMENT_MISMATCH = "instrument_mismatch"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    NON_POSITIVE = "non_positive"
    EDGE_TOO_SMALL = "edge_too_small"
    STALE_BOOK = "stale_book"
    BOOK_SKEW = "book_skew"
    BUY_QUOTE_INVENTORY = "buy_quote_inventory"
    SELL_BASE_INVENTORY = "sell_base_inventory"


@dataclass(frozen=True, slots=True)
class CrossVenueOpportunity:
    instrument: Instrument
    buy_venue: str
    sell_venue: str
    base_quantity: Decimal
    buy_average_price: Decimal
    sell_average_price: Decimal
    buy_quote_required: Decimal
    sell_quote_proceeds: Decimal
    gross_spread_quote: Decimal
    fee_cost_quote: Decimal
    execution_reserve_quote: Decimal
    rebalance_reserve_quote: Decimal
    net_profit_quote: Decimal
    net_edge_bps: Decimal
    created_time_ms: int
    buy_book_age_ms: int
    sell_book_age_ms: int
    book_skew_ms: int


@dataclass(frozen=True, slots=True)
class CrossVenueDecision:
    approved: bool
    reason: CrossVenueReason


class CrossVenueStrategy:
    """Research model for pre-funded simultaneous buy/sell across two venues."""

    name = "prefunded_cross_venue"

    def __init__(
        self,
        *,
        config: CrossVenueConfig,
        costs: tuple[VenueCostProfile, ...],
    ) -> None:
        self.config = config
        self.costs = {profile.venue: profile for profile in costs}
        if len(self.costs) != len(costs):
            raise ValueError("venue cost profiles must be unique")

    def evaluate_direction(
        self,
        *,
        buy_book: NormalizedBook,
        sell_book: NormalizedBook,
        now_ms: int,
    ) -> CrossVenueOpportunity | None:
        if buy_book.venue == sell_book.venue:
            return None
        if buy_book.instrument != sell_book.instrument:
            return None
        buy_cost = self.costs.get(buy_book.venue)
        sell_cost = self.costs.get(sell_book.venue)
        if buy_cost is None or sell_cost is None:
            raise ValueError("missing venue cost profile")

        base_quantity = self.config.target_notional_quote / buy_book.snapshot.mid_price
        try:
            buy_fill = simulate_base_fill(buy_book.snapshot, Side.BUY, base_quantity)
            sell_fill = simulate_base_fill(sell_book.snapshot, Side.SELL, base_quantity)
        except InsufficientLiquidity:
            return None

        buy_quote = buy_fill.quote_quantity
        sell_quote = sell_fill.quote_quantity
        gross_spread = sell_quote - buy_quote
        fee_cost = (
            buy_quote * buy_cost.taker_fee_bps / TEN_THOUSAND
            + sell_quote * sell_cost.taker_fee_bps / TEN_THOUSAND
        )
        reference_notional = (buy_quote + sell_quote) / Decimal("2")
        execution_reserve = (
            reference_notional
            * (buy_cost.execution_reserve_bps + sell_cost.execution_reserve_bps)
            / TEN_THOUSAND
        )
        rebalance_reserve = (
            reference_notional
            * (buy_cost.rebalance_reserve_bps + sell_cost.rebalance_reserve_bps)
            / TEN_THOUSAND
        )
        net_profit = gross_spread - fee_cost - execution_reserve - rebalance_reserve
        net_edge = net_profit / reference_notional * TEN_THOUSAND
        buy_age = buy_book.snapshot.age_ms(now_ms)
        sell_age = sell_book.snapshot.age_ms(now_ms)
        skew = abs(
            buy_book.snapshot.received_time_ms - sell_book.snapshot.received_time_ms
        )
        return CrossVenueOpportunity(
            instrument=buy_book.instrument,
            buy_venue=buy_book.venue,
            sell_venue=sell_book.venue,
            base_quantity=base_quantity,
            buy_average_price=buy_fill.average_price,
            sell_average_price=sell_fill.average_price,
            buy_quote_required=buy_quote,
            sell_quote_proceeds=sell_quote,
            gross_spread_quote=gross_spread,
            fee_cost_quote=fee_cost,
            execution_reserve_quote=execution_reserve,
            rebalance_reserve_quote=rebalance_reserve,
            net_profit_quote=net_profit,
            net_edge_bps=net_edge,
            created_time_ms=now_ms,
            buy_book_age_ms=buy_age,
            sell_book_age_ms=sell_age,
            book_skew_ms=skew,
        )

    def assess(
        self,
        opportunity: CrossVenueOpportunity,
        *,
        inventories: tuple[VenueInventory, ...] = (),
    ) -> CrossVenueDecision:
        if opportunity.buy_venue == opportunity.sell_venue:
            return CrossVenueDecision(False, CrossVenueReason.SAME_VENUE)
        if max(opportunity.buy_book_age_ms, opportunity.sell_book_age_ms) > self.config.max_book_age_ms:
            return CrossVenueDecision(False, CrossVenueReason.STALE_BOOK)
        if opportunity.book_skew_ms > self.config.max_book_skew_ms:
            return CrossVenueDecision(False, CrossVenueReason.BOOK_SKEW)
        if opportunity.net_profit_quote <= ZERO:
            return CrossVenueDecision(False, CrossVenueReason.NON_POSITIVE)
        if opportunity.net_edge_bps < self.config.min_net_edge_bps:
            return CrossVenueDecision(False, CrossVenueReason.EDGE_TOO_SMALL)

        if inventories:
            by_venue = {inventory.venue: inventory for inventory in inventories}
            buy_inventory = by_venue.get(opportunity.buy_venue)
            sell_inventory = by_venue.get(opportunity.sell_venue)
            if (
                buy_inventory is None
                or buy_inventory.quote_available < opportunity.buy_quote_required
            ):
                return CrossVenueDecision(False, CrossVenueReason.BUY_QUOTE_INVENTORY)
            if (
                sell_inventory is None
                or sell_inventory.base_available < opportunity.base_quantity
            ):
                return CrossVenueDecision(False, CrossVenueReason.SELL_BASE_INVENTORY)
        return CrossVenueDecision(True, CrossVenueReason.APPROVED)

    def best_direction(
        self,
        first: NormalizedBook,
        second: NormalizedBook,
        *,
        now_ms: int,
        inventories: tuple[VenueInventory, ...] = (),
    ) -> tuple[CrossVenueOpportunity, CrossVenueDecision] | None:
        if first.instrument != second.instrument or first.venue == second.venue:
            return None
        candidates: list[tuple[CrossVenueOpportunity, CrossVenueDecision]] = []
        for buy_book, sell_book in ((first, second), (second, first)):
            opportunity = self.evaluate_direction(
                buy_book=buy_book,
                sell_book=sell_book,
                now_ms=now_ms,
            )
            if opportunity is None:
                continue
            candidates.append(
                (opportunity, self.assess(opportunity, inventories=inventories))
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0].net_edge_bps, reverse=True)
        return candidates[0]
