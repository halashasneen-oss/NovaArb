from __future__ import annotations

from decimal import Decimal

from novaarb.allocator import AllocationConfig
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.shadow import AssetBalance, MultiAssetInventoryLedger, ShadowRiskConfig
from novaarb.shadow_live import ShadowLiveConfig, ShadowLiveCoordinator
from novaarb.shadow_scanner import MultiInstrumentShadowEngine
from novaarb.venue import Instrument, NormalizedBook


def _book(*, venue: str, bid: str, ask: str, received_ms: int) -> NormalizedBook:
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    snapshot = OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )
    return NormalizedBook(
        instrument=instrument,
        venue_symbol="BTCUSDT",
        snapshot=snapshot,
    )


def _costs() -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile("alpha", Decimal("1")),
        VenueCostProfile("beta", Decimal("1")),
    )


def _engine_and_event():
    inventory = MultiAssetInventoryLedger(
        (
            AssetBalance("alpha", "USDT", Decimal("1000")),
            AssetBalance("alpha", "BTC", Decimal("0")),
            AssetBalance("beta", "USDT", Decimal("100")),
            AssetBalance("beta", "BTC", Decimal("2")),
        )
    )
    engine = MultiInstrumentShadowEngine(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=500,
            max_book_skew_ms=150,
        ),
        costs=_costs(),
        inventory=inventory,
    )
    assert not engine.process_book(
        _book(venue="alpha", bid="99", ask="100", received_ms=1_000),
        now_ms=1_000,
    )
    events = engine.process_book(
        _book(venue="beta", bid="102", ask="103", received_ms=1_001),
        now_ms=1_001,
    )
    assert len(events) == 1
    assert events[0].decision.approved is True
    return engine, events[0]


def _coordinator(engine: MultiInstrumentShadowEngine) -> ShadowLiveCoordinator:
    return ShadowLiveCoordinator(
        engine=engine,
        costs=_costs(),
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("1000"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("2"),
        ),
        risk_config=ShadowRiskConfig(
            max_daily_loss_quote=Decimal("25"),
            max_venue_concentration_pct=Decimal("0.95"),
            max_asset_concentration_pct=Decimal("0.95"),
        ),
        live_config=ShadowLiveConfig(
            allocation_window_ms=50,
            execution_delay_ms=50,
            heartbeat_interval_ms=1_000,
            max_data_staleness_ms=500,
        ),
    )


def test_live_shadow_coordinator_applies_hypothetical_fill_only() -> None:
    engine, event = _engine_and_event()
    coordinator = _coordinator(engine)

    result = coordinator.process_batch((event,), now_ms=1_050)

    assert result.observed == 1
    assert result.selected == 1
    assert result.executed == 1
    assert result.trades[0].conservative_net_profit_quote > 0
    assert coordinator.executed_trades == 1
    assert coordinator.conservative_net_profit_quote > 0
    assert engine.inventory.balance("alpha", "BTC") > 0
    assert engine.inventory.balance("beta", "BTC") < Decimal("2")
    metrics = coordinator.metrics(now_ms=1_050)
    assert metrics["mode"] == "shadow_public_data_only"
    assert metrics["executed_trades"] == 1
    assert metrics["data_healthy"] is True


def test_live_shadow_committed_fill_keeps_adverse_post_signal_move() -> None:
    engine, event = _engine_and_event()
    coordinator = _coordinator(engine)

    engine.process_book(
        _book(venue="alpha", bid="102", ask="103", received_ms=1_050),
        now_ms=1_050,
    )
    engine.process_book(
        _book(venue="beta", bid="101", ask="102", received_ms=1_052),
        now_ms=1_052,
    )
    result = coordinator.process_batch((event,), now_ms=1_052)

    assert result.executed == 1
    assert result.trades[0].realized_edge_bps < 0
    assert result.trades[0].conservative_net_profit_quote < 0
    assert coordinator.profitable_trades == 0
    assert coordinator.conservative_net_profit_quote < 0
    assert coordinator.guard.daily_pnl(1_052) < 0


def test_live_shadow_coordinator_halts_on_stale_public_data() -> None:
    engine, event = _engine_and_event()
    coordinator = _coordinator(engine)

    result = coordinator.process_batch((event,), now_ms=2_000)

    assert result.executed == 0
    assert result.risk_halts == 1
    assert coordinator.risk_halts == 1
    assert coordinator.kill_reasons == {"data_unhealthy": 1}


def test_live_shadow_coordinator_halts_after_daily_loss_limit() -> None:
    engine, event = _engine_and_event()
    coordinator = _coordinator(engine)
    coordinator.guard.record_realized_pnl(
        timestamp_ms=1_050,
        pnl_quote=Decimal("-25"),
    )

    result = coordinator.process_batch((event,), now_ms=1_050)

    assert result.executed == 0
    assert result.risk_halts == 1
    assert coordinator.kill_reasons == {"daily_loss": 1}
