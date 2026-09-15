from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from novaarb.allocator import AllocationConfig
from novaarb.candidate_bus import (
    CROSS_VENUE_STRATEGY,
    FUNDING_STRATEGY,
    CandidateEnvelope,
    ShadowCandidateBus,
)
from novaarb.cross_venue import CrossVenueOpportunity, VenueCostProfile
from novaarb.cross_venue_execution import reprice_committed_cross_venue
from novaarb.domain import MarketType, OrderBookSnapshot, TEN_THOUSAND, ZERO
from novaarb.funding import FundingCarryConfig, FundingCarryStrategy, FundingSnapshot
from novaarb.funding_shadow import (
    FundingShadowBook,
    FundingShadowClose,
    FundingShadowConfig,
    FundingShadowSettlement,
)
from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.shadow import (
    AssetMark,
    MultiAssetInventoryLedger,
    PortfolioMark,
    ShadowKillSwitchReason,
    ShadowRiskConfig,
    ShadowRiskGuard,
    VenueMark,
)
from novaarb.shadow_scanner import MultiInstrumentShadowEngine


@dataclass(frozen=True, slots=True)
class FundingAssetMap:
    symbol: str
    base_asset: str
    quote_asset: str

    def __post_init__(self) -> None:
        if not self.symbol or not self.base_asset or not self.quote_asset:
            raise ValueError("funding asset mapping fields are required")
        if self.base_asset.upper() == self.quote_asset.upper():
            raise ValueError("funding base and quote assets must differ")


@dataclass(frozen=True, slots=True)
class UnifiedShadowConfig:
    funding_intervals: int = 1

    def __post_init__(self) -> None:
        if self.funding_intervals <= 0:
            raise ValueError("funding_intervals must be positive")


@dataclass(frozen=True, slots=True)
class UnifiedCrossVenueFill:
    opportunity_id: str
    timestamp_ms: int
    instrument: str
    buy_venue: str
    sell_venue: str
    detected_edge_bps: Decimal
    realized_edge_bps: Decimal
    net_profit_quote: Decimal


@dataclass(frozen=True, slots=True)
class UnifiedBatchResult:
    observed: int
    selected: int
    cross_venue_executed: int
    funding_opened: int
    decayed: int
    inventory_rejections: int
    risk_halts: int
    unsupported_selected: int
    cross_venue_fills: tuple[UnifiedCrossVenueFill, ...]


class UnifiedShadowCoordinator:
    """Research-only shared portfolio for cross-venue and funding positions.

    The coordinator keeps long-lived funding positions visible to the common allocator through
    reserved capital, occupied position slots and shared market-resource locks. Cross-venue
    opportunities remain immediate hypothetical fills. All inputs are public/research objects;
    there is no authenticated order, withdrawal or transfer client in this layer.
    """

    def __init__(
        self,
        *,
        engine: MultiInstrumentShadowEngine,
        costs: tuple[VenueCostProfile, ...],
        quote_asset: str,
        allocation_config: AllocationConfig,
        funding_assets: tuple[FundingAssetMap, ...],
        funding_strategy_config: FundingCarryConfig | None = None,
        funding_shadow_config: FundingShadowConfig | None = None,
        risk_config: ShadowRiskConfig | None = None,
        config: UnifiedShadowConfig | None = None,
    ) -> None:
        self.engine = engine
        self.inventory: MultiAssetInventoryLedger = engine.inventory
        self.quote_asset = quote_asset.upper()
        self.cost_profiles = costs
        self.costs = {item.venue: item for item in costs}
        if len(self.costs) != len(costs) or len(self.costs) < 2:
            raise ValueError("unified shadow costs require at least two unique venues")
        if set(self.costs) != set(engine.cost_venues):
            raise ValueError("unified shadow costs must match cross-venue engine costs")

        self.config = config or UnifiedShadowConfig()
        self.candidate_bus = ShadowCandidateBus(allocation_config)
        self.guard = ShadowRiskGuard(risk_config or ShadowRiskConfig())
        self.funding_strategy = FundingCarryStrategy(
            funding_strategy_config or FundingCarryConfig()
        )
        self.funding_book = FundingShadowBook(
            inventory=self.inventory,
            config=funding_shadow_config or FundingShadowConfig(),
        )
        self.funding_assets = {item.symbol.upper(): item for item in funding_assets}
        if len(self.funding_assets) != len(funding_assets):
            raise ValueError("funding symbols must be unique")

        self.funding_snapshots: dict[str, FundingSnapshot] = {}
        self.funding_books: dict[tuple[str, MarketType], OrderBookSnapshot] = {}
        self.cross_venue_realized_pnl = ZERO
        self.funding_realized_pnl = ZERO
        self.cross_venue_fills: list[UnifiedCrossVenueFill] = []
        self.funding_closes: list[FundingShadowClose] = []
        self.allocation_reasons: Counter[str] = Counter()
        self.kill_reasons: Counter[str] = Counter()
        self.decayed = 0
        self.inventory_rejections = 0
        self.risk_halts = 0
        self.unsupported_selected = 0

    def observe_funding(self, snapshot: FundingSnapshot) -> None:
        symbol = snapshot.symbol.upper()
        current = self.funding_snapshots.get(symbol)
        if current is None or snapshot.received_time_ms >= current.received_time_ms:
            self.funding_snapshots[symbol] = snapshot
        self.funding_book.observe_funding(snapshot)

    def observe_funding_book(self, snapshot: OrderBookSnapshot) -> None:
        if snapshot.market not in {MarketType.SPOT, MarketType.PERPETUAL}:
            raise ValueError("funding shadow book must be Spot or Perpetual")
        symbol = snapshot.symbol.upper()
        if symbol not in self.funding_assets:
            raise ValueError(f"missing funding asset mapping for {symbol}")
        if snapshot.venue != self.funding_book.config.venue:
            raise ValueError("funding book venue must match funding shadow venue")
        self.funding_books[(symbol, snapshot.market)] = snapshot

    def _cross_venue_prices(self) -> dict[str, Decimal]:
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
        prices = self._cross_venue_prices()
        for mapping in self.funding_assets.values():
            spot = self.funding_books.get((mapping.symbol.upper(), MarketType.SPOT))
            if spot is not None and mapping.quote_asset.upper() == self.quote_asset:
                prices.setdefault(mapping.base_asset.upper(), spot.mid_price)

        balances = self.inventory.snapshot()
        required = {
            item.asset
            for item in balances
            if item.asset != self.quote_asset and item.quantity > ZERO
        }
        if not required <= set(prices):
            return None
        free_mark = self.inventory.mark_to_quote(
            quote_asset=self.quote_asset,
            prices_in_quote=prices,
        )

        venue_values = {item.venue: item.value_quote for item in free_mark.venue_values}
        asset_values = {item.asset: item.value_quote for item in free_mark.asset_values}
        total = free_mark.total_value_quote

        for position in self.funding_book.positions.values():
            spot = self.funding_books.get((position.symbol.upper(), MarketType.SPOT))
            perpetual = self.funding_books.get(
                (position.symbol.upper(), MarketType.PERPETUAL)
            )
            if spot is None or perpetual is None:
                return None
            value = self.funding_book.mark_position_quote(
                position.position_id,
                spot_mid_price=spot.mid_price,
                futures_mid_price=perpetual.mid_price,
            )
            venue_values[position.venue] = venue_values.get(position.venue, ZERO) + value
            asset_values[self.quote_asset] = (
                asset_values.get(self.quote_asset, ZERO) + value
            )
            total += value

        return PortfolioMark(
            quote_asset=self.quote_asset,
            total_value_quote=total,
            venue_values=tuple(
                VenueMark(venue=venue, value_quote=value)
                for venue, value in sorted(venue_values.items())
            ),
            asset_values=tuple(
                AssetMark(asset=asset, value_quote=value)
                for asset, value in sorted(asset_values.items())
            ),
        )

    def _assess_risk(self, *, now_ms: int, data_healthy: bool) -> bool:
        mark = self.portfolio_mark()
        if mark is None:
            self.risk_halts += 1
            self.kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += 1
            return False
        decision = self.guard.assess(
            timestamp_ms=now_ms,
            mark=mark,
            data_healthy=data_healthy,
        )
        if decision.allowed:
            return True
        self.risk_halts += 1
        self.kill_reasons[decision.reason.value] += 1
        return False

    def _cross_venue_fee(
        self,
        opportunity: CrossVenueOpportunity,
        venue: str,
    ) -> Decimal:
        profile = self.costs[venue]
        notional = (
            opportunity.buy_quote_required
            if venue == opportunity.buy_venue
            else opportunity.sell_quote_proceeds
        )
        return notional * profile.taker_fee_bps / TEN_THOUSAND

    def _execute_cross_venue(
        self,
        envelope: CandidateEnvelope,
        *,
        now_ms: int,
    ) -> UnifiedCrossVenueFill | None:
        detected = envelope.payload
        if not isinstance(detected, CrossVenueOpportunity):
            raise TypeError("cross-venue envelope payload must be CrossVenueOpportunity")
        instrument = detected.instrument
        buy_book = self.engine.books.get(
            (instrument.canonical_symbol, detected.buy_venue)
        )
        sell_book = self.engine.books.get(
            (instrument.canonical_symbol, detected.sell_venue)
        )
        if buy_book is None or sell_book is None:
            return None

        realized = reprice_committed_cross_venue(
            detected,
            buy_book=buy_book,
            sell_book=sell_book,
            costs=self.cost_profiles,
            now_ms=now_ms,
        )
        if realized is None:
            return None
        if (
            max(realized.buy_book_age_ms, realized.sell_book_age_ms)
            > self.engine.strategy.config.max_book_age_ms
            or realized.book_skew_ms > self.engine.strategy.config.max_book_skew_ms
        ):
            return None

        trade = ExecutedCrossVenueTrade(
            buy_venue=realized.buy_venue,
            sell_venue=realized.sell_venue,
            base_quantity=realized.base_quantity,
            buy_quote_spent=realized.buy_quote_required,
            sell_quote_received=realized.sell_quote_proceeds,
            buy_fee_quote=self._cross_venue_fee(realized, realized.buy_venue),
            sell_fee_quote=self._cross_venue_fee(realized, realized.sell_venue),
        )
        self.engine.apply_shadow_fill(
            trade,
            base_asset=instrument.base_asset,
            quote_asset=instrument.quote_asset,
        )
        self.guard.record_realized_pnl(
            timestamp_ms=now_ms,
            pnl_quote=realized.net_profit_quote,
        )
        self.cross_venue_realized_pnl += realized.net_profit_quote
        fill = UnifiedCrossVenueFill(
            opportunity_id=envelope.candidate.opportunity_id,
            timestamp_ms=now_ms,
            instrument=instrument.canonical_symbol,
            buy_venue=realized.buy_venue,
            sell_venue=realized.sell_venue,
            detected_edge_bps=detected.net_edge_bps,
            realized_edge_bps=realized.net_edge_bps,
            net_profit_quote=realized.net_profit_quote,
        )
        self.cross_venue_fills.append(fill)
        return fill

    def _open_funding(
        self,
        envelope: CandidateEnvelope,
        *,
        now_ms: int,
    ) -> bool:
        projected = envelope.payload
        if envelope.family != FUNDING_STRATEGY:
            raise ValueError("funding opener requires a funding envelope")
        mapping = self.funding_assets.get(projected.symbol.upper())
        if mapping is None:
            raise ValueError(f"missing funding asset mapping for {projected.symbol}")
        spot = self.funding_books.get((projected.symbol.upper(), MarketType.SPOT))
        perpetual = self.funding_books.get(
            (projected.symbol.upper(), MarketType.PERPETUAL)
        )
        funding = self.funding_snapshots.get(projected.symbol.upper())
        if spot is None or perpetual is None or funding is None:
            return False

        repriced = self.funding_strategy.evaluate(
            spot,
            perpetual,
            funding,
            now_ms=now_ms,
        )
        if repriced is None or not self.funding_strategy.assess(repriced).approved:
            return False
        if repriced.reference_notional_usdt > projected.reference_notional_usdt:
            scale = repriced.reference_notional_usdt / projected.reference_notional_usdt
            if envelope.candidate.capital_required_usdt * scale > (
                envelope.candidate.capital_required_usdt * Decimal("1.01")
            ):
                return False

        updated = CandidateEnvelope(
            candidate=envelope.candidate,
            family=envelope.family,
            payload=repriced,
        )
        self.funding_book.open_from_envelope(
            updated,
            base_asset=mapping.base_asset,
            quote_asset=mapping.quote_asset,
            funding_intervals=self.config.funding_intervals,
        )
        return True

    def process_batch(
        self,
        envelopes: tuple[CandidateEnvelope, ...],
        *,
        now_ms: int,
        data_healthy: bool = True,
    ) -> UnifiedBatchResult:
        if not envelopes:
            return UnifiedBatchResult(0, 0, 0, 0, 0, 0, 0, 0, ())
        if not self._assess_risk(now_ms=now_ms, data_healthy=data_healthy):
            return UnifiedBatchResult(
                len(envelopes),
                0,
                0,
                0,
                0,
                0,
                len(envelopes),
                0,
                (),
            )

        allocation = self.candidate_bus.allocate(
            envelopes,
            reserved_capital_usdt=self.funding_book.reserved_capital_quote,
            open_positions=self.funding_book.open_positions,
            used_resources=self.funding_book.used_resources,
            strategy_capital_usdt=self.funding_book.strategy_capital(),
            strategy_positions=self.funding_book.strategy_positions(),
        )
        for decision in allocation.decisions:
            if not decision.selected:
                self.allocation_reasons[decision.reason.value] += 1

        cross_fills: list[UnifiedCrossVenueFill] = []
        funding_opened = 0
        decayed = 0
        inventory_rejections = 0
        risk_halts = 0
        unsupported = 0

        for envelope in allocation.selected:
            if not self._assess_risk(now_ms=now_ms, data_healthy=data_healthy):
                risk_halts += 1
                continue
            try:
                if envelope.family == CROSS_VENUE_STRATEGY:
                    fill = self._execute_cross_venue(envelope, now_ms=now_ms)
                    if fill is None:
                        decayed += 1
                    else:
                        cross_fills.append(fill)
                elif envelope.family == FUNDING_STRATEGY:
                    if self._open_funding(envelope, now_ms=now_ms):
                        funding_opened += 1
                    else:
                        decayed += 1
                else:
                    unsupported += 1
            except ValueError:
                inventory_rejections += 1

        self.decayed += decayed
        self.inventory_rejections += inventory_rejections
        self.unsupported_selected += unsupported
        return UnifiedBatchResult(
            observed=len(envelopes),
            selected=len(allocation.selected),
            cross_venue_executed=len(cross_fills),
            funding_opened=funding_opened,
            decayed=decayed,
            inventory_rejections=inventory_rejections,
            risk_halts=risk_halts,
            unsupported_selected=unsupported,
            cross_venue_fills=tuple(cross_fills),
        )

    def settle_funding(
        self,
        *,
        now_ms: int,
    ) -> tuple[FundingShadowSettlement, ...]:
        return self.funding_book.settle_due(now_ms=now_ms)

    def close_ready_funding(
        self,
        *,
        now_ms: int,
    ) -> tuple[FundingShadowClose, ...]:
        closes: list[FundingShadowClose] = []
        ready = [
            position
            for position in self.funding_book.positions.values()
            if position.ready_to_close
        ]
        for position in ready:
            spot = self.funding_books.get((position.symbol.upper(), MarketType.SPOT))
            perpetual = self.funding_books.get(
                (position.symbol.upper(), MarketType.PERPETUAL)
            )
            if spot is None or perpetual is None:
                continue
            try:
                close = self.funding_book.close_ready(
                    position.position_id,
                    spot_book=spot,
                    perpetual_book=perpetual,
                    now_ms=now_ms,
                )
            except ValueError:
                continue
            self.guard.record_realized_pnl(
                timestamp_ms=now_ms,
                pnl_quote=close.realized_net_profit_quote,
            )
            self.funding_realized_pnl += close.realized_net_profit_quote
            self.funding_closes.append(close)
            closes.append(close)
        return tuple(closes)

    @property
    def realized_pnl_quote(self) -> Decimal:
        return self.cross_venue_realized_pnl + self.funding_realized_pnl
