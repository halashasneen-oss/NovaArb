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


def _book(*, venue: str, bid: str, ask: str, received_ms: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def test_shadow_replay_executes_committed_quantity_even_after_edge_turns_negative(
    tmp_path,
) -> None:
    path = tmp_path / "adverse.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_000))
    recorder.append_book(_book(venue="beta", bid="99", ask="100", received_ms=1_001))
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_100))
    recorder.append_book(_book(venue="beta", bid="102", ask="103", received_ms=1_101))
    # The allocation window has elapsed. This later alpha book makes the
    # originally attractive alpha->beta direction negative at execution time.
    recorder.append_book(_book(venue="alpha", bid="102", ask="103", received_ms=1_200))

    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    summary = replay_shadow_portfolio(
        str(path),
        instruments=(
            VenueInstrument("alpha", "BTCUSDT", instrument),
            VenueInstrument("beta", "BTCUSDT", instrument),
        ),
        costs=(
            VenueCostProfile("alpha", Decimal("1")),
            VenueCostProfile("beta", Decimal("1")),
        ),
        balances=(
            AssetBalance("alpha", "USDT", Decimal("1000")),
            AssetBalance("alpha", "BTC", Decimal("0")),
            AssetBalance("beta", "USDT", Decimal("100")),
            AssetBalance("beta", "BTC", Decimal("2")),
        ),
        quote_asset="USDT",
        allocation_config=AllocationConfig(
            total_capital_usdt=Decimal("1000"),
            max_positions=2,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("2"),
        ),
        strategy_config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=500,
            max_book_skew_ms=150,
        ),
        risk_config=ShadowRiskConfig(
            max_daily_loss_quote=Decimal("25"),
            max_venue_concentration_pct=Decimal("0.95"),
            max_asset_concentration_pct=Decimal("0.95"),
        ),
        replay_config=ShadowPortfolioReplayConfig(
            allocation_window_ms=50,
            signal_cooldown_ms=1_000,
            max_data_staleness_ms=500,
        ),
    )

    assert summary.detected_signals == 1
    assert summary.allocator_selected == 1
    assert summary.executed_trades == 1
    assert summary.profitable_trades == 0
    assert summary.conservative_net_profit_quote < 0
    assert summary.trades[0].detected_edge_bps > 0
    assert summary.trades[0].realized_edge_bps < 0
    assert summary.trades[0].edge_decay_bps > 0
