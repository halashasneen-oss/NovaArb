from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from novaarb.candidate_bus import CandidateEnvelope, FUNDING_STRATEGY
from novaarb.domain import MarketType, OrderBookSnapshot, Side, TEN_THOUSAND, ZERO
from novaarb.funding import FundingCarryOpportunity, FundingSnapshot
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill
from novaarb.shadow import AssetBalanceDelta, MultiAssetInventoryLedger


@dataclass(frozen=True, slots=True)
class FundingShadowConfig:
    venue: str = "binance"
    spot_taker_fee_bps: Decimal = Decimal("10")
    futures_taker_fee_bps: Decimal = Decimal("5")
    max_book_age_ms: int = 1_000
    max_book_skew_ms: int = 500

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue is required")
        if min(self.spot_taker_fee_bps, self.futures_taker_fee_bps) < ZERO:
            raise ValueError("funding shadow fees cannot be negative")
        if self.max_book_age_ms < 0 or self.max_book_skew_ms < 0:
            raise ValueError("funding shadow timing limits cannot be negative")


@dataclass(slots=True)
class FundingShadowPosition:
    position_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    venue: str
    base_quantity: Decimal
    spot_entry_price: Decimal
    futures_entry_price: Decimal
    reference_notional_quote: Decimal
    entry_fees_quote: Decimal
    capital_reserved_quote: Decimal
    opened_at_ms: int
    next_funding_time_ms: int
    funding_intervals_required: int
    resource_keys: tuple[str, ...]
    settled_intervals: int = 0
    funding_received_quote: Decimal = ZERO
    last_settlement_time_ms: int = 0

    @property
    def ready_to_close(self) -> bool:
        return self.settled_intervals >= self.funding_intervals_required


@dataclass(frozen=True, slots=True)
class FundingShadowSettlement:
    position_id: str
    symbol: str
    settlement_time_ms: int
    funding_rate_bps: Decimal
    mark_price: Decimal
    payment_quote: Decimal
    settled_intervals: int
    ready_to_close: bool


@dataclass(frozen=True, slots=True)
class FundingShadowClose:
    position_id: str
    symbol: str
    opened_at_ms: int
    closed_at_ms: int
    capital_reserved_quote: Decimal
    spot_pnl_quote: Decimal
    futures_pnl_quote: Decimal
    funding_received_quote: Decimal
    entry_fees_quote: Decimal
    exit_fees_quote: Decimal
    realized_net_profit_quote: Decimal
    realized_edge_bps: Decimal
    holding_ms: int


class FundingShadowBook:
    """Stateful research-only long-Spot/short-Perpetual position lifecycle.

    Selected funding candidates reserve quote capital from the shared shadow inventory. The
    reserved amount is held outside free inventory until close, preventing other strategies from
    spending it. Funding is accrued from observed public snapshots, then the position is closed
    against visible Spot and Perpetual depth. No authenticated exchange action exists here.
    """

    def __init__(
        self,
        *,
        inventory: MultiAssetInventoryLedger,
        config: FundingShadowConfig | None = None,
    ) -> None:
        self.inventory = inventory
        self.config = config or FundingShadowConfig()
        self.positions: dict[str, FundingShadowPosition] = {}
        self.latest_funding_by_target: dict[tuple[str, int], FundingSnapshot] = {}
        self.next_targets: dict[tuple[str, int], int] = {}

    @property
    def reserved_capital_quote(self) -> Decimal:
        return sum(
            (position.capital_reserved_quote for position in self.positions.values()),
            ZERO,
        )

    @property
    def used_resources(self) -> frozenset[str]:
        return frozenset(
            resource
            for position in self.positions.values()
            for resource in position.resource_keys
        )

    @property
    def open_positions(self) -> int:
        return len(self.positions)

    def strategy_capital(self) -> dict[str, Decimal]:
        if not self.positions:
            return {}
        return {FUNDING_STRATEGY: self.reserved_capital_quote}

    def strategy_positions(self) -> dict[str, int]:
        if not self.positions:
            return {}
        return {FUNDING_STRATEGY: len(self.positions)}

    def open_from_envelope(
        self,
        envelope: CandidateEnvelope,
        *,
        base_asset: str,
        quote_asset: str,
        funding_intervals: int,
    ) -> FundingShadowPosition:
        if envelope.family != FUNDING_STRATEGY:
            raise ValueError("funding shadow book requires a funding candidate envelope")
        if not isinstance(envelope.payload, FundingCarryOpportunity):
            raise TypeError("funding envelope payload must be FundingCarryOpportunity")
        if funding_intervals <= 0:
            raise ValueError("funding_intervals must be positive")
        candidate = envelope.candidate
        if candidate.opportunity_id in self.positions:
            raise ValueError("funding shadow position id already exists")

        opportunity = envelope.payload
        venue = self.config.venue
        capital = candidate.capital_required_usdt
        if self.inventory.balance(venue, quote_asset) < capital:
            raise ValueError("insufficient free quote inventory for funding position")
        self.inventory.apply_deltas(
            (
                AssetBalanceDelta(venue, quote_asset, -capital),
            )
        )
        position = FundingShadowPosition(
            position_id=candidate.opportunity_id,
            symbol=opportunity.symbol,
            base_asset=base_asset.upper(),
            quote_asset=quote_asset.upper(),
            venue=venue,
            base_quantity=opportunity.base_quantity,
            spot_entry_price=opportunity.spot_entry_price,
            futures_entry_price=opportunity.futures_entry_price,
            reference_notional_quote=opportunity.reference_notional_usdt,
            entry_fees_quote=opportunity.entry_fees_usdt,
            capital_reserved_quote=capital,
            opened_at_ms=opportunity.created_time_ms,
            next_funding_time_ms=0,
            funding_intervals_required=funding_intervals,
            resource_keys=candidate.resource_keys,
        )
        self.positions[position.position_id] = position
        return position

    def observe_funding(self, snapshot: FundingSnapshot) -> None:
        key = (snapshot.symbol, snapshot.next_funding_time_ms)
        current = self.latest_funding_by_target.get(key)
        if current is None or snapshot.received_time_ms >= current.received_time_ms:
            self.latest_funding_by_target[key] = snapshot

        previous_targets = sorted(
            target
            for symbol, target in self.latest_funding_by_target
            if symbol == snapshot.symbol and target < snapshot.next_funding_time_ms
        )
        if previous_targets:
            self.next_targets[(snapshot.symbol, previous_targets[-1])] = snapshot.next_funding_time_ms

        for position in self.positions.values():
            if position.symbol != snapshot.symbol or position.ready_to_close:
                continue
            if position.next_funding_time_ms == 0 and snapshot.next_funding_time_ms > position.opened_at_ms:
                position.next_funding_time_ms = snapshot.next_funding_time_ms

    def settle_due(self, *, now_ms: int) -> tuple[FundingShadowSettlement, ...]:
        settlements: list[FundingShadowSettlement] = []
        for position in sorted(self.positions.values(), key=lambda item: item.position_id):
            while (
                not position.ready_to_close
                and position.next_funding_time_ms > 0
                and now_ms >= position.next_funding_time_ms
            ):
                target = position.next_funding_time_ms
                snapshot = self.latest_funding_by_target.get((position.symbol, target))
                if snapshot is None or snapshot.received_time_ms > target:
                    break
                payment = (
                    position.base_quantity * snapshot.mark_price * snapshot.funding_rate
                )
                position.funding_received_quote += payment
                position.settled_intervals += 1
                position.last_settlement_time_ms = target
                if position.ready_to_close:
                    position.next_funding_time_ms = 0
                else:
                    next_target = self.next_targets.get((position.symbol, target), 0)
                    position.next_funding_time_ms = next_target
                settlements.append(
                    FundingShadowSettlement(
                        position_id=position.position_id,
                        symbol=position.symbol,
                        settlement_time_ms=target,
                        funding_rate_bps=snapshot.funding_rate_bps,
                        mark_price=snapshot.mark_price,
                        payment_quote=payment,
                        settled_intervals=position.settled_intervals,
                        ready_to_close=position.ready_to_close,
                    )
                )
                if position.next_funding_time_ms == 0:
                    break
        return tuple(settlements)

    def mark_position_quote(
        self,
        position_id: str,
        *,
        spot_mid_price: Decimal,
        futures_mid_price: Decimal,
    ) -> Decimal:
        position = self.positions[position_id]
        if min(spot_mid_price, futures_mid_price) <= ZERO:
            raise ValueError("mark prices must be positive")
        spot_pnl = position.base_quantity * (spot_mid_price - position.spot_entry_price)
        futures_pnl = position.base_quantity * (
            position.futures_entry_price - futures_mid_price
        )
        return (
            position.capital_reserved_quote
            + spot_pnl
            + futures_pnl
            + position.funding_received_quote
            - position.entry_fees_quote
        )

    def close_ready(
        self,
        position_id: str,
        *,
        spot_book: OrderBookSnapshot,
        perpetual_book: OrderBookSnapshot,
        now_ms: int,
    ) -> FundingShadowClose:
        position = self.positions[position_id]
        if not position.ready_to_close:
            raise ValueError("funding position has not completed required settlements")
        if spot_book.venue != position.venue or perpetual_book.venue != position.venue:
            raise ValueError("funding exit books must match position venue")
        if spot_book.symbol != position.symbol or perpetual_book.symbol != position.symbol:
            raise ValueError("funding exit books must match position symbol")
        if spot_book.market is not MarketType.SPOT:
            raise ValueError("funding exit requires Spot book")
        if perpetual_book.market is not MarketType.PERPETUAL:
            raise ValueError("funding exit requires Perpetual book")
        if max(spot_book.age_ms(now_ms), perpetual_book.age_ms(now_ms)) > self.config.max_book_age_ms:
            raise ValueError("funding exit books are stale")
        if abs(spot_book.received_time_ms - perpetual_book.received_time_ms) > self.config.max_book_skew_ms:
            raise ValueError("funding exit book skew exceeds limit")

        try:
            spot_exit = simulate_base_fill(spot_book, Side.SELL, position.base_quantity)
            futures_exit = simulate_base_fill(
                perpetual_book,
                Side.BUY,
                position.base_quantity,
            )
        except InsufficientLiquidity as exc:
            raise ValueError("insufficient funding exit liquidity") from exc

        spot_entry_quote = position.base_quantity * position.spot_entry_price
        futures_entry_quote = position.base_quantity * position.futures_entry_price
        spot_pnl = spot_exit.quote_quantity - spot_entry_quote
        futures_pnl = futures_entry_quote - futures_exit.quote_quantity
        exit_fees = (
            spot_exit.quote_quantity * self.config.spot_taker_fee_bps / TEN_THOUSAND
            + futures_exit.quote_quantity * self.config.futures_taker_fee_bps / TEN_THOUSAND
        )
        net = (
            spot_pnl
            + futures_pnl
            + position.funding_received_quote
            - position.entry_fees_quote
            - exit_fees
        )
        released = position.capital_reserved_quote + net
        if released < ZERO:
            raise ValueError("funding loss exceeds reserved shadow capital")
        if released > ZERO:
            self.inventory.apply_deltas(
                (
                    AssetBalanceDelta(position.venue, position.quote_asset, released),
                )
            )

        close = FundingShadowClose(
            position_id=position.position_id,
            symbol=position.symbol,
            opened_at_ms=position.opened_at_ms,
            closed_at_ms=now_ms,
            capital_reserved_quote=position.capital_reserved_quote,
            spot_pnl_quote=spot_pnl,
            futures_pnl_quote=futures_pnl,
            funding_received_quote=position.funding_received_quote,
            entry_fees_quote=position.entry_fees_quote,
            exit_fees_quote=exit_fees,
            realized_net_profit_quote=net,
            realized_edge_bps=(
                net / position.reference_notional_quote * TEN_THOUSAND
            ),
            holding_ms=max(0, now_ms - position.opened_at_ms),
        )
        del self.positions[position_id]
        return close
