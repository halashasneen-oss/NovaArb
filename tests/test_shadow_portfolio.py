from __future__ import annotations

from decimal import Decimal

from novaarb.allocator import AllocationConfig
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder
from novaarb.shadow import AssetBalance, ShadowRiskConfig
from novaarb.shadow_portfolio import (
    ShadowPortfolioReplayConfig,
    replay_shadow_portfolio,
)
from novaarb.venue import Instrument, VenueInstrument


def _book(
    *,
    venue: str,
    symbol: str,
    bid: str,
    ask: str,
    received_ms: int,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("100")),),
        asks=(BookLevel(Decimal(ask), Decimal("100")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _write_capture(path) -> None:
    recorder = ResearchRecorder(path)
    # Prime both symbols on both venues without an arbitrage edge so the
    # shared portfolio can be marked before any shadow fill is considered.
    recorder.append_book(
        _book(
            venue="alpha",
            symbol="BTCUSDT",
            bid="100",
            ask="101",
            received_ms=1_000,
        )
    )
    recorder.append_book(
        _book(
            venue="beta",
            symbol="BTCUSDT",
            bid="100",
            ask="101",
            received_ms=1_001,
        )
    )
    recorder.append_book(
        _book(
            venue="alpha",
            symbol="ETHUSDT",
            bid="10",
            ask="10.1",
            received_ms=1_002,
        )
    )
    recorder.append_book(
        _book(
            venue="beta",
            symbol="ETHUSDT",
            bid="10",
            ask="10.1",
            received_ms=1_003,
        )
    )

    # Create simultaneous profitable dislocations in different instruments.
    recorder.append_book(
        _book(
            venue="alpha",
            symbol="BTCUSDT",
            bid="99",
            ask="100",
            received_ms=1_100,
        )
    )
    recorder.append_book(
        _book(
            venue="beta",
            symbol="BTCUSDT",
            bid="102",
            ask="103",
            received_ms=1_101,
        )
    )
    recorder.append_book(
        _book(
            venue="alpha",
            symbol="ETHUSDT",
            bid="9.9",
            ask="10",
            received_ms=1_102,
        )
    )
    recorder.append_book(
        _book(
            venue="beta",
            symbol="ETHUSDT",
            bid="10.3",
            ask="10.4",
            received_ms=1_103,
        )
    )


def _instruments() -> tuple[VenueInstrument, ...]:
    btc = Instrument("BTC", "USDT", MarketType.SPOT)
    eth = Instrument("ETH", "USDT", MarketType.SPOT)
    return (
        VenueInstrument("alpha", "BTCUSDT", btc),
        VenueInstrument("beta", "BTCUSDT", btc),
        VenueInstrument("alpha", "ETHUSDT", eth),
        VenueInstrument("beta", "ETHUSDT", eth),
    )


def _costs() -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile("alpha", Decimal("1")),
        VenueCostProfile("beta", Decimal("1")),
    )


def _balances() -> tuple[AssetBalance, ...]:
    return (
        AssetBalance("alpha", "USDT", Decimal("1000")),
        AssetBalance("alpha", "BTC", Decimal("0")),
        AssetBalance("alpha", "ETH", Decimal("0")),
        AssetBalance("beta", "USDT", Decimal("100")),
        AssetBalance("beta", "BTC", Decimal("2")),
        AssetBalance("beta", "ETH", Decimal("10")),
    )


def _strategy() -> CrossVenueConfig:
    return CrossVenueConfig(
        target_notional_quote=Decimal("100"),
        min_net_edge_bps=Decimal("2"),
        max_book_age_ms=500,
        max_book_skew_ms=150,
    )


def _risk() -> ShadowRiskConfig:
    return ShadowRiskConfig(
        max_daily_loss_quote=Decimal("100"),
        max_venue_concentration_pct=Decimal("0.90"),
        max_asset_concentration_pct=Decimal("0.95"),
    )


def test_shadow_portfolio_replays_multiple_instruments_with_shared_inventory(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    _write_capture(path)

    summary = replay_shadow_portfolio(
        str(path),
        instruments=_instruments(),
        costs=_costs(),
        balances=_balances(),
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("2000"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("2"),
        ),
        strategy_config=_strategy(),
        risk_config=_risk(),
        replay_config=ShadowPortfolioReplayConfig(
            allocation_window_ms=50,
            signal_cooldown_ms=1_000,
            max_data_staleness_ms=500,
        ),
    )

    assert summary.snapshots == 8
    assert summary.detected_signals == 2
    assert summary.allocator_selected == 2
    assert summary.allocator_rejections == 0
    assert summary.executed_trades == 2
    assert summary.profitable_trades == 2
    assert summary.conservative_net_profit_quote > 0
    assert summary.inventory_rejections == 0
    assert summary.risk_halts == 0
    assert {item.instrument for item in summary.symbols} == {
        "BTC/USDT:spot",
        "ETH/USDT:spot",
    }
    balances = {(item.venue, item.asset): item.quantity for item in summary.ending_balances}
    assert balances[("alpha", "BTC")] > 0
    assert balances[("alpha", "ETH")] > 0
    assert balances[("beta", "BTC")] < Decimal("2")
    assert balances[("beta", "ETH")] < Decimal("10")


def test_shadow_portfolio_allocator_prevents_capital_overcommit(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    _write_capture(path)

    summary = replay_shadow_portfolio(
        str(path),
        instruments=_instruments(),
        costs=_costs(),
        balances=_balances(),
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("250"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("2"),
        ),
        strategy_config=_strategy(),
        risk_config=_risk(),
        replay_config=ShadowPortfolioReplayConfig(
            allocation_window_ms=50,
            signal_cooldown_ms=1_000,
            max_data_staleness_ms=500,
        ),
    )

    assert summary.detected_signals == 2
    assert summary.allocator_selected == 1
    assert summary.allocator_rejections == 1
    assert summary.executed_trades == 1
    assert summary.allocation_rejection_reasons == {"insufficient_capital": 1}


def test_shadow_portfolio_halts_when_one_venue_feed_is_stale(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    _write_capture(path)

    summary = replay_shadow_portfolio(
        str(path),
        instruments=_instruments(),
        costs=_costs(),
        balances=_balances(),
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("2000"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("2"),
        ),
        strategy_config=_strategy(),
        risk_config=_risk(),
        replay_config=ShadowPortfolioReplayConfig(
            allocation_window_ms=50,
            signal_cooldown_ms=1_000,
            max_data_staleness_ms=0,
        ),
    )

    assert summary.detected_signals == 2
    assert summary.executed_trades == 0
    assert summary.risk_halts == 2
    assert summary.kill_switch_reasons == {"data_unhealthy": 2}
