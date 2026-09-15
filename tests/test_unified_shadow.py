from __future__ import annotations

from decimal import Decimal

from novaarb.allocator import AllocationConfig, AllocationReason
from novaarb.candidate_bus import cross_venue_envelope, funding_envelope
from novaarb.cross_venue import (
    CrossVenueConfig,
    CrossVenueOpportunity,
    VenueCostProfile,
)
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.funding import FundingCarryConfig, FundingCarryOpportunity, FundingSnapshot
from novaarb.funding_shadow import FundingShadowConfig
from novaarb.shadow import AssetBalance, MultiAssetInventoryLedger, ShadowRiskConfig
from novaarb.shadow_scanner import MultiInstrumentShadowEngine
from novaarb.unified_shadow import (
    FundingAssetMap,
    UnifiedShadowConfig,
    UnifiedShadowCoordinator,
)
from novaarb.venue import Instrument, NormalizedBook


def _book(
    *,
    venue: str,
    symbol: str,
    market: MarketType,
    bid: str,
    ask: str,
    received_ms: int,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol=symbol,
        market=market,
        bids=(BookLevel(Decimal(bid), Decimal("20")),),
        asks=(BookLevel(Decimal(ask), Decimal("20")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _normalized(
    *,
    venue: str,
    base: str,
    bid: str,
    ask: str,
    received_ms: int,
) -> NormalizedBook:
    symbol = f"{base}USDT"
    return NormalizedBook(
        instrument=Instrument(base, "USDT", MarketType.SPOT),
        venue_symbol=symbol,
        snapshot=_book(
            venue=venue,
            symbol=symbol,
            market=MarketType.SPOT,
            bid=bid,
            ask=ask,
            received_ms=received_ms,
        ),
    )


def _funding_opportunity(symbol: str, *, created_ms: int = 1_000) -> FundingCarryOpportunity:
    return FundingCarryOpportunity(
        symbol=symbol,
        strategy="funding_carry_long_spot_short_perp",
        base_quantity=Decimal("1"),
        spot_entry_price=Decimal("100.1"),
        futures_entry_price=Decimal("101"),
        reference_notional_usdt=Decimal("100"),
        funding_rate_bps=Decimal("100"),
        current_basis_bps=Decimal("90"),
        expected_funding_usdt=Decimal("0.5"),
        entry_execution_cost_usdt=Decimal("0"),
        entry_fees_usdt=Decimal("0.02"),
        exit_reserve_usdt=Decimal("0.01"),
        basis_risk_reserve_usdt=Decimal("0.01"),
        expected_net_profit_usdt=Decimal("0.46"),
        expected_net_edge_bps=Decimal("46"),
        created_time_ms=created_ms,
        spot_book_age_ms=0,
        futures_book_age_ms=0,
        funding_age_ms=0,
    )


def _coordinator() -> UnifiedShadowCoordinator:
    inventory = MultiAssetInventoryLedger(
        (
            AssetBalance("binance", "USDT", Decimal("1000")),
            AssetBalance("binance", "BTC", Decimal("1")),
            AssetBalance("binance", "ETH", Decimal("2")),
            AssetBalance("bybit", "USDT", Decimal("1000")),
            AssetBalance("bybit", "BTC", Decimal("1")),
            AssetBalance("bybit", "ETH", Decimal("2")),
        )
    )
    costs = (
        VenueCostProfile("binance", Decimal("1")),
        VenueCostProfile("bybit", Decimal("1")),
    )
    engine = MultiInstrumentShadowEngine(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=100,
        ),
        costs=costs,
        inventory=inventory,
    )
    engine.process_book(
        _normalized(
            venue="binance",
            base="BTC",
            bid="100",
            ask="100.1",
            received_ms=1_000,
        ),
        now_ms=1_000,
    )
    engine.process_book(
        _normalized(
            venue="bybit",
            base="BTC",
            bid="100",
            ask="100.1",
            received_ms=1_001,
        ),
        now_ms=1_001,
    )
    engine.process_book(
        _normalized(
            venue="binance",
            base="ETH",
            bid="50",
            ask="50.1",
            received_ms=1_000,
        ),
        now_ms=1_000,
    )
    engine.process_book(
        _normalized(
            venue="bybit",
            base="ETH",
            bid="51",
            ask="51.1",
            received_ms=1_001,
        ),
        now_ms=1_001,
    )
    coordinator = UnifiedShadowCoordinator(
        engine=engine,
        costs=costs,
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("1500"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("1"),
        ),
        funding_assets=(FundingAssetMap("BTCUSDT", "BTC", "USDT"),),
        funding_strategy_config=FundingCarryConfig(
            target_notional_usdt=Decimal("100"),
            funding_intervals=1,
            funding_haircut=Decimal("0.5"),
            spot_taker_fee_bps=Decimal("1"),
            futures_taker_fee_bps=Decimal("1"),
            exit_market_reserve_bps=Decimal("1"),
            basis_risk_reserve_bps=Decimal("1"),
            min_net_edge_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=100,
            max_funding_age_ms=10_000,
        ),
        funding_shadow_config=FundingShadowConfig(
            spot_taker_fee_bps=Decimal("1"),
            futures_taker_fee_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=100,
        ),
        risk_config=ShadowRiskConfig(
            max_daily_loss_quote=Decimal("1000"),
            max_venue_concentration_pct=Decimal("1"),
            max_asset_concentration_pct=Decimal("1"),
            max_venue_weight_drift_pct=Decimal("1"),
        ),
        config=UnifiedShadowConfig(funding_intervals=1),
    )
    coordinator.observe_funding_book(
        _book(
            venue="binance",
            symbol="BTCUSDT",
            market=MarketType.SPOT,
            bid="100",
            ask="100.1",
            received_ms=1_000,
        )
    )
    coordinator.observe_funding_book(
        _book(
            venue="binance",
            symbol="BTCUSDT",
            market=MarketType.PERPETUAL,
            bid="101",
            ask="101.1",
            received_ms=1_001,
        )
    )
    coordinator.observe_funding(
        FundingSnapshot(
            symbol="BTCUSDT",
            funding_rate=Decimal("0.01"),
            next_funding_time_ms=5_000,
            mark_price=Decimal("101"),
            index_price=Decimal("100.9"),
            received_time_ms=900,
        )
    )
    return coordinator


def test_unified_shadow_keeps_funding_capital_and_resource_locks_visible() -> None:
    coordinator = _coordinator()
    funding = funding_envelope(
        _funding_opportunity("BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )

    opened = coordinator.process_batch((funding,), now_ms=1_002)

    assert opened.funding_opened == 1
    assert coordinator.funding_book.open_positions == 1
    assert coordinator.funding_book.reserved_capital_quote == Decimal("200")
    assert coordinator.inventory.balance("binance", "USDT") == Decimal("800")
    mark = coordinator.portfolio_mark()
    assert mark is not None
    assert mark.total_value_quote > Decimal("2200")

    btc = Instrument("BTC", "USDT", MarketType.SPOT)
    conflict = CrossVenueOpportunity(
        instrument=btc,
        buy_venue="binance",
        sell_venue="bybit",
        base_quantity=Decimal("0.5"),
        buy_average_price=Decimal("100.1"),
        sell_average_price=Decimal("101"),
        buy_quote_required=Decimal("50.05"),
        sell_quote_proceeds=Decimal("50.5"),
        gross_spread_quote=Decimal("0.45"),
        fee_cost_quote=Decimal("0.01"),
        execution_reserve_quote=Decimal("0"),
        rebalance_reserve_quote=Decimal("0"),
        net_profit_quote=Decimal("0.44"),
        net_edge_bps=Decimal("87"),
        created_time_ms=1_003,
        buy_book_age_ms=0,
        sell_book_age_ms=0,
        book_skew_ms=1,
    )
    result = coordinator.process_batch(
        (cross_venue_envelope(conflict, sequence=2),),
        now_ms=1_003,
    )

    assert result.selected == 0
    assert coordinator.allocation_reasons[AllocationReason.RESOURCE_CONFLICT.value] == 1


def test_unified_shadow_settles_funding_and_executes_unrelated_cross_venue() -> None:
    coordinator = _coordinator()
    funding = funding_envelope(
        _funding_opportunity("BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )
    assert coordinator.process_batch((funding,), now_ms=1_002).funding_opened == 1

    eth = Instrument("ETH", "USDT", MarketType.SPOT)
    cross = CrossVenueOpportunity(
        instrument=eth,
        buy_venue="binance",
        sell_venue="bybit",
        base_quantity=Decimal("1"),
        buy_average_price=Decimal("50.1"),
        sell_average_price=Decimal("51"),
        buy_quote_required=Decimal("50.1"),
        sell_quote_proceeds=Decimal("51"),
        gross_spread_quote=Decimal("0.9"),
        fee_cost_quote=Decimal("0.02"),
        execution_reserve_quote=Decimal("0"),
        rebalance_reserve_quote=Decimal("0"),
        net_profit_quote=Decimal("0.88"),
        net_edge_bps=Decimal("174"),
        created_time_ms=1_004,
        buy_book_age_ms=0,
        sell_book_age_ms=0,
        book_skew_ms=1,
    )
    cross_result = coordinator.process_batch(
        (cross_venue_envelope(cross, sequence=2),),
        now_ms=1_004,
    )
    assert cross_result.cross_venue_executed == 1
    assert coordinator.cross_venue_realized_pnl > 0

    settlements = coordinator.settle_funding(now_ms=5_000)
    assert len(settlements) == 1
    assert settlements[0].ready_to_close is True

    coordinator.observe_funding_book(
        _book(
            venue="binance",
            symbol="BTCUSDT",
            market=MarketType.SPOT,
            bid="101",
            ask="101.1",
            received_ms=5_010,
        )
    )
    coordinator.observe_funding_book(
        _book(
            venue="binance",
            symbol="BTCUSDT",
            market=MarketType.PERPETUAL,
            bid="101.1",
            ask="101.2",
            received_ms=5_011,
        )
    )
    closes = coordinator.close_ready_funding(now_ms=5_011)

    assert len(closes) == 1
    assert closes[0].realized_net_profit_quote > 0
    assert coordinator.funding_book.open_positions == 0
    assert coordinator.funding_realized_pnl == closes[0].realized_net_profit_quote
    assert coordinator.realized_pnl_quote > coordinator.funding_realized_pnl
