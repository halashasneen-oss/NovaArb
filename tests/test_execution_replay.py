from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.execution import LatencyProfile
from novaarb.execution_replay import replay_triangle_execution
from novaarb.research import ResearchRecorder
from novaarb.symbols import SymbolRules
from novaarb.triangular import TriangleConfig, TriangleRoute


def _rule(symbol: str, base: str, quote: str) -> SymbolRules:
    return SymbolRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        status="TRADING",
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.000001"),
        min_notional=Decimal("1"),
    )


def _book(symbol: str, bid: str, ask: str, received_ms: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("1000")),),
        asks=(BookLevel(Decimal(ask), Decimal("1000")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def test_execution_replay_compares_latency_profiles(tmp_path) -> None:
    path = tmp_path / "triangle.jsonl"
    recorder = ResearchRecorder(path)
    rules = (
        _rule("BTCUSDT", "BTC", "USDT"),
        _rule("ETHBTC", "ETH", "BTC"),
        _rule("ETHUSDT", "ETH", "USDT"),
    )
    route = TriangleRoute("USDT", "BTC", "ETH", ("BTCUSDT", "ETHBTC", "ETHUSDT"))
    config = TriangleConfig(
        starting_amount=Decimal("100"),
        taker_fee_bps=Decimal("0"),
        execution_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("1"),
        max_book_age_ms=500,
        max_book_skew_ms=200,
    )
    recorder.append_metadata(
        "triangle_session",
        {
            "anchor": "USDT",
            "allowed_assets": ["USDT", "BTC", "ETH"],
            "config": config,
            "rules": rules,
            "routes": (route,),
        },
    )
    for book in (
        _book("BTCUSDT", "99", "100", 1_000),
        _book("ETHBTC", "0.49", "0.50", 1_000),
        _book("ETHUSDT", "60", "61", 1_000),
        _book("BTCUSDT", "99", "100", 1_025),
        _book("ETHBTC", "0.49", "0.50", 1_050),
        _book("ETHUSDT", "59", "60", 1_075),
        _book("BTCUSDT", "99", "100", 1_100),
        _book("ETHBTC", "0.49", "0.50", 1_200),
        _book("ETHUSDT", "49", "50", 1_300),
    ):
        recorder.append_book(book)

    summary = replay_triangle_execution(
        str(path),
        profiles=(
            LatencyProfile("fast", 25, 25, 50),
            LatencyProfile("slow", 100, 100, 50),
        ),
    )

    assert summary.snapshots == 9
    assert summary.approved_signals > 0
    assert len(summary.profiles) == 2
    fast, slow = summary.profiles
    assert fast.detected_signals == slow.detected_signals
    assert fast.completed >= slow.completed
    assert fast.first_leg_ms == 25
    assert slow.first_leg_ms == 100
    assert fast.median_detected_edge_bps >= 0
    assert fast.p90_edge_decay_bps >= fast.median_edge_decay_bps
    assert Decimal("0") <= fast.profitable_signal_rate <= Decimal("1")
    assert len(summary.route_stats) == 2
    assert {stats.profile_name for stats in summary.route_stats} == {"fast", "slow"}
    assert all(stats.route_id == "USDT>BTC>ETH>USDT" for stats in summary.route_stats)
