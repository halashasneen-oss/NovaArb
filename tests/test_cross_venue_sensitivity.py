from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_replay import CrossVenueLatencyProfile, CrossVenueReplayConfig
from novaarb.cross_venue_sensitivity import run_cross_venue_sensitivity_matrix
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder


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


def test_cross_venue_sensitivity_penalizes_fee_and_transfer_costs(tmp_path) -> None:
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

    report = run_cross_venue_sensitivity_matrix(
        str(path),
        base_asset="BTC",
        quote_asset="USDT",
        base_costs=(
            VenueCostProfile("alpha", Decimal("1")),
            VenueCostProfile("beta", Decimal("1")),
        ),
        inventories=(
            VenueInventory("alpha", Decimal("2"), Decimal("1000")),
            VenueInventory("beta", Decimal("2"), Decimal("1000")),
        ),
        fee_tiers_bps=(Decimal("1"), Decimal("5")),
        transfer_costs_bps=(Decimal("0"), Decimal("10")),
        strategy_config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=150,
        ),
        latency=CrossVenueLatencyProfile(buy_ms=50, sell_ms=50, max_wait_ms=50),
        replay=CrossVenueReplayConfig(route_cooldown_ms=1_000),
    )

    assert report.fee_tiers_bps == (Decimal("1"), Decimal("5"))
    assert report.transfer_costs_bps == (Decimal("0"), Decimal("10"))
    assert len(report.points) == 4

    by_key = {
        (point.taker_fee_bps, point.transfer_cost_bps): point
        for point in report.points
    }
    low_fee = by_key[(Decimal("1"), Decimal("0"))]
    high_fee = by_key[(Decimal("5"), Decimal("0"))]
    transfer_free = by_key[(Decimal("1"), Decimal("0"))]
    transfer_costly = by_key[(Decimal("1"), Decimal("10"))]

    assert low_fee.completed_trades == 1
    assert high_fee.completed_trades == 1
    assert low_fee.total_net_profit_quote > high_fee.total_net_profit_quote
    assert transfer_free.rebalance_cost_quote == 0
    assert transfer_costly.rebalance_cost_quote > 0
    assert transfer_costly.net_after_rebalance_quote < transfer_free.net_after_rebalance_quote
