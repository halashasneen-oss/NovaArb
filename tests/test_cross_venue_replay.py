from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_replay import (
    CrossVenueLatencyProfile,
    CrossVenueReplayConfig,
    replay_cross_venue_capture,
)
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.inventory import RebalanceConfig
from novaarb.research import ResearchRecorder


def _book(
    *,
    venue: str,
    bid: str,
    ask: str,
    received_ms: int,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _costs() -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile("alpha", Decimal("1")),
        VenueCostProfile("beta", Decimal("1")),
    )


def _inventories() -> tuple[VenueInventory, ...]:
    return (
        VenueInventory("alpha", Decimal("2"), Decimal("1000")),
        VenueInventory("beta", Decimal("2"), Decimal("1000")),
    )


def test_cross_venue_replay_reprices_after_latency_and_tracks_inventory(tmp_path) -> None:
    path = tmp_path / "cross.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_000))
    recorder.append_book(_book(venue="beta", bid="102", ask="103", received_ms=1_000))
    recorder.append_book(
        _book(venue="alpha", bid="99.2", ask="100.2", received_ms=1_060)
    )
    recorder.append_book(
        _book(venue="beta", bid="101.8", ask="102.8", received_ms=1_065)
    )

    summary = replay_cross_venue_capture(
        str(path),
        base_asset="BTC",
        quote_asset="USDT",
        costs=_costs(),
        inventories=_inventories(),
        strategy_config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=150,
        ),
        latency=CrossVenueLatencyProfile(buy_ms=50, sell_ms=50, max_wait_ms=50),
        replay=CrossVenueReplayConfig(route_cooldown_ms=1_000),
        rebalance_config=RebalanceConfig(
            base_transfer_cost_bps=Decimal("5"),
            quote_transfer_cost_bps=Decimal("2"),
        ),
    )

    assert summary.detected_signals == 1
    assert summary.completed_trades == 1
    assert summary.profitable_trades == 1
    assert summary.total_net_profit_quote > 0
    assert summary.median_realized_edge_bps < summary.median_detected_edge_bps
    assert summary.median_edge_decay_bps > 0
    balances = {item.venue: item for item in summary.ending_inventories}
    assert balances["alpha"].base_available > Decimal("2")
    assert balances["alpha"].quote_available < Decimal("1000")
    assert balances["beta"].base_available < Decimal("2")
    assert balances["beta"].quote_available > Decimal("1000")
    assert summary.rebalance_plan.instructions
    assert summary.rebalance_plan.total_estimated_cost_quote > 0


def test_cross_venue_replay_counts_missing_future_books(tmp_path) -> None:
    path = tmp_path / "cross.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_000))
    recorder.append_book(_book(venue="beta", bid="102", ask="103", received_ms=1_000))

    summary = replay_cross_venue_capture(
        str(path),
        base_asset="BTC",
        quote_asset="USDT",
        costs=_costs(),
        inventories=_inventories(),
        strategy_config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
        ),
        latency=CrossVenueLatencyProfile(buy_ms=50, sell_ms=50, max_wait_ms=10),
    )

    assert summary.detected_signals == 1
    assert summary.missing_future_books == 1
    assert summary.completed_trades == 0
    assert summary.total_net_profit_quote == 0
