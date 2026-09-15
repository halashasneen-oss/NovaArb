from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.execution import LatencyProfile
from novaarb.fee_sensitivity import replay_fee_sensitivity
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


def _book(symbol: str, bid: str, ask: str, timestamp: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("1000")),),
        asks=(BookLevel(Decimal(ask), Decimal("1000")),),
        event_time_ms=timestamp,
        received_time_ms=timestamp,
    )


def test_fee_sensitivity_reduces_edge_and_signal_count(tmp_path) -> None:
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
    for timestamp in (1_000, 1_050, 1_100, 1_150):
        recorder.append_book(_book("BTCUSDT", "99", "100", timestamp))
        recorder.append_book(_book("ETHBTC", "0.49", "0.50", timestamp))
        recorder.append_book(_book("ETHUSDT", "60", "61", timestamp))

    summary = replay_fee_sensitivity(
        str(path),
        fee_tiers_bps=(Decimal("0"), Decimal("1000")),
        latency=LatencyProfile("test", 0, 0, 100),
    )

    zero_fee, high_fee = summary.tiers
    assert summary.snapshots == 12
    assert zero_fee.theoretical_signals > 0
    assert zero_fee.theoretical_signals >= high_fee.theoretical_signals
    assert zero_fee.median_detected_edge_bps >= high_fee.median_detected_edge_bps
    assert zero_fee.total_net_profit >= high_fee.total_net_profit
