from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from novaarb.cross_venue import (
    CrossVenueConfig,
    CrossVenueOpportunity,
    CrossVenueStrategy,
    VenueCostProfile,
    VenueInventory,
)
from novaarb.domain import MarketType, TEN_THOUSAND, ZERO, OrderBookSnapshot
from novaarb.inventory import (
    ExecutedCrossVenueTrade,
    InventoryLedger,
    InventoryRebalancePlanner,
    RebalanceConfig,
    RebalancePlan,
    VenueInventoryTarget,
)
from novaarb.research import iter_records, snapshot_from_record
from novaarb.venue import Instrument, NormalizedBook


@dataclass(frozen=True, slots=True)
class CrossVenueLatencyProfile:
    buy_ms: int = 50
    sell_ms: int = 50
    max_wait_ms: int = 250

    def __post_init__(self) -> None:
        if min(self.buy_ms, self.sell_ms, self.max_wait_ms) < 0:
            raise ValueError("latency values cannot be negative")


@dataclass(frozen=True, slots=True)
class CrossVenueReplayConfig:
    route_cooldown_ms: int = 1_000

    def __post_init__(self) -> None:
        if self.route_cooldown_ms < 0:
            raise ValueError("route_cooldown_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class CrossVenueReplayTrade:
    detected_time_ms: int
    buy_venue: str
    sell_venue: str
    detected_edge_bps: Decimal
    realized_edge_bps: Decimal
    edge_decay_bps: Decimal
    realized_net_profit_quote: Decimal
    buy_execution_time_ms: int
    sell_execution_time_ms: int


@dataclass(frozen=True, slots=True)
class CrossVenueReplaySummary:
    detected_signals: int
    inventory_rejections: int
    missing_future_books: int
    completed_trades: int
    profitable_trades: int
    profitable_trade_rate: Decimal
    total_net_profit_quote: Decimal
    median_detected_edge_bps: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal
    ending_inventories: tuple[VenueInventory, ...]
    rebalance_plan: RebalancePlan
    trades: tuple[CrossVenueReplayTrade, ...]


@dataclass(frozen=True, slots=True)
class _TimedBook:
    book: NormalizedBook
    received_time_ms: int


class _VenueBookIndex:
    def __init__(self, books: tuple[NormalizedBook, ...]) -> None:
        ordered = sorted(books, key=lambda item: item.snapshot.received_time_ms)
        self.books = ordered
        self.timestamps = [item.snapshot.received_time_ms for item in ordered]

    def first_at_or_after(
        self,
        target_ms: int,
        *,
        max_wait_ms: int,
    ) -> NormalizedBook | None:
        index = bisect_left(self.timestamps, target_ms)
        if index >= len(self.books):
            return None
        book = self.books[index]
        if book.snapshot.received_time_ms - target_ms > max_wait_ms:
            return None
        return book


def _decimal_median(values: list[Decimal]) -> Decimal:
    return Decimal(median(values)) if values else ZERO


def _normalized_book(
    snapshot: OrderBookSnapshot,
    *,
    instrument: Instrument,
) -> NormalizedBook:
    if snapshot.market is not instrument.market:
        raise ValueError("capture market does not match replay instrument")
    return NormalizedBook(
        instrument=instrument,
        venue_symbol=snapshot.symbol,
        snapshot=snapshot,
    )


def _initial_targets(
    inventories: tuple[VenueInventory, ...],
) -> tuple[VenueInventoryTarget, ...]:
    total_base = sum((item.base_available for item in inventories), ZERO)
    total_quote = sum((item.quote_available for item in inventories), ZERO)
    if total_base <= ZERO or total_quote <= ZERO:
        raise ValueError("replay inventories require positive total base and quote")
    return tuple(
        VenueInventoryTarget(
            venue=item.venue,
            base_share=item.base_available / total_base,
            quote_share=item.quote_available / total_quote,
        )
        for item in inventories
    )


def _fee_for_venue(
    opportunity: CrossVenueOpportunity,
    *,
    venue: str,
    costs: dict[str, VenueCostProfile],
) -> Decimal:
    profile = costs[venue]
    if venue == opportunity.buy_venue:
        notional = opportunity.buy_quote_required
    elif venue == opportunity.sell_venue:
        notional = opportunity.sell_quote_proceeds
    else:
        raise ValueError("venue is not part of opportunity")
    return notional * profile.taker_fee_bps / TEN_THOUSAND


def replay_cross_venue_capture(
    path: str,
    *,
    base_asset: str,
    quote_asset: str,
    costs: tuple[VenueCostProfile, ...],
    inventories: tuple[VenueInventory, ...],
    strategy_config: CrossVenueConfig = CrossVenueConfig(),
    latency: CrossVenueLatencyProfile = CrossVenueLatencyProfile(),
    replay: CrossVenueReplayConfig = CrossVenueReplayConfig(),
    rebalance_config: RebalanceConfig = RebalanceConfig(),
) -> CrossVenueReplaySummary:
    """Replays public cross-venue books with delayed simultaneous-leg execution."""

    if len(costs) < 2:
        raise ValueError("cross-venue replay requires at least two venue cost profiles")
    cost_map = {item.venue: item for item in costs}
    if len(cost_map) != len(costs):
        raise ValueError("venue cost profiles must be unique")
    inventory_venues = {item.venue for item in inventories}
    if inventory_venues != set(cost_map):
        raise ValueError("inventory venues must match cost-profile venues")

    instrument = Instrument(base_asset, quote_asset, MarketType.SPOT)
    books: list[NormalizedBook] = []
    for record in iter_records(path):
        if record.get("kind") != "book":
            continue
        snapshot = snapshot_from_record(record)
        if snapshot.venue not in cost_map:
            continue
        if snapshot.market is not MarketType.SPOT:
            continue
        books.append(_normalized_book(snapshot, instrument=instrument))

    books.sort(key=lambda item: item.snapshot.received_time_ms)
    by_venue: dict[str, list[NormalizedBook]] = {venue: [] for venue in cost_map}
    for book in books:
        by_venue[book.venue].append(book)
    if any(not venue_books for venue_books in by_venue.values()):
        raise ValueError("capture does not contain Spot books for every replay venue")

    indices = {
        venue: _VenueBookIndex(tuple(venue_books))
        for venue, venue_books in by_venue.items()
    }
    strategy = CrossVenueStrategy(config=strategy_config, costs=costs)
    ledger = InventoryLedger(inventories)
    latest: dict[str, NormalizedBook] = {}
    last_signal_ms: dict[tuple[str, str], int] = {}

    detected_signals = 0
    inventory_rejections = 0
    missing_future_books = 0
    completed: list[CrossVenueReplayTrade] = []

    for current in books:
        latest[current.venue] = current
        for other_venue, other in tuple(latest.items()):
            if other_venue == current.venue:
                continue
            detected_at = max(
                current.snapshot.received_time_ms,
                other.snapshot.received_time_ms,
            )
            result = strategy.best_direction(
                current,
                other,
                now_ms=detected_at,
                inventories=ledger.snapshot(),
            )
            if result is None:
                continue
            detected, decision = result
            if not decision.approved:
                continue

            route_key = (detected.buy_venue, detected.sell_venue)
            previous = last_signal_ms.get(route_key)
            if previous is not None and detected_at - previous < replay.route_cooldown_ms:
                continue
            last_signal_ms[route_key] = detected_at
            detected_signals += 1

            current_inventory = {item.venue: item for item in ledger.snapshot()}
            buy_inventory = current_inventory[detected.buy_venue]
            sell_inventory = current_inventory[detected.sell_venue]
            if (
                buy_inventory.quote_available < detected.buy_quote_required
                or sell_inventory.base_available < detected.base_quantity
            ):
                inventory_rejections += 1
                continue

            buy_target_ms = detected_at + latency.buy_ms
            sell_target_ms = detected_at + latency.sell_ms
            buy_book = indices[detected.buy_venue].first_at_or_after(
                buy_target_ms,
                max_wait_ms=latency.max_wait_ms,
            )
            sell_book = indices[detected.sell_venue].first_at_or_after(
                sell_target_ms,
                max_wait_ms=latency.max_wait_ms,
            )
            if buy_book is None or sell_book is None:
                missing_future_books += 1
                continue

            realized = strategy.evaluate_direction(
                buy_book=buy_book,
                sell_book=sell_book,
                now_ms=max(
                    buy_book.snapshot.received_time_ms,
                    sell_book.snapshot.received_time_ms,
                ),
            )
            if realized is None:
                missing_future_books += 1
                continue

            buy_fee = _fee_for_venue(
                realized,
                venue=realized.buy_venue,
                costs=cost_map,
            )
            sell_fee = _fee_for_venue(
                realized,
                venue=realized.sell_venue,
                costs=cost_map,
            )
            try:
                ledger.apply(
                    ExecutedCrossVenueTrade(
                        buy_venue=realized.buy_venue,
                        sell_venue=realized.sell_venue,
                        base_quantity=realized.base_quantity,
                        buy_quote_spent=realized.buy_quote_required,
                        sell_quote_received=realized.sell_quote_proceeds,
                        buy_fee_quote=buy_fee,
                        sell_fee_quote=sell_fee,
                    )
                )
            except ValueError:
                inventory_rejections += 1
                continue

            completed.append(
                CrossVenueReplayTrade(
                    detected_time_ms=detected_at,
                    buy_venue=realized.buy_venue,
                    sell_venue=realized.sell_venue,
                    detected_edge_bps=detected.net_edge_bps,
                    realized_edge_bps=realized.net_edge_bps,
                    edge_decay_bps=detected.net_edge_bps - realized.net_edge_bps,
                    realized_net_profit_quote=realized.net_profit_quote,
                    buy_execution_time_ms=buy_book.snapshot.received_time_ms,
                    sell_execution_time_ms=sell_book.snapshot.received_time_ms,
                )
            )

    ending = ledger.snapshot()
    reference_prices = [book.snapshot.mid_price for book in books]
    reference_price = Decimal(median(reference_prices)) if reference_prices else ZERO
    planner = InventoryRebalancePlanner(
        targets=_initial_targets(inventories),
        config=rebalance_config,
    )
    rebalance_plan = planner.plan(
        ending,
        base_reference_price_quote=reference_price,
    )

    profits = [trade.realized_net_profit_quote for trade in completed]
    detected_edges = [trade.detected_edge_bps for trade in completed]
    realized_edges = [trade.realized_edge_bps for trade in completed]
    edge_decay = [trade.edge_decay_bps for trade in completed]
    profitable = sum(1 for profit in profits if profit > ZERO)
    completed_count = len(completed)
    profitable_rate = (
        Decimal(profitable) / Decimal(completed_count)
        if completed_count
        else ZERO
    )
    return CrossVenueReplaySummary(
        detected_signals=detected_signals,
        inventory_rejections=inventory_rejections,
        missing_future_books=missing_future_books,
        completed_trades=completed_count,
        profitable_trades=profitable,
        profitable_trade_rate=profitable_rate,
        total_net_profit_quote=sum(profits, ZERO),
        median_detected_edge_bps=_decimal_median(detected_edges),
        median_realized_edge_bps=_decimal_median(realized_edges),
        median_edge_decay_bps=_decimal_median(edge_decay),
        ending_inventories=ending,
        rebalance_plan=rebalance_plan,
        trades=tuple(completed),
    )
