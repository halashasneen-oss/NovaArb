from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.execution import LatencyProfile
from novaarb.research import ResearchRecorder
from novaarb.research_report import build_triangle_report, report_json
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
        event_time_ms=received_ms - 5,
        received_time_ms=received_ms,
    )


def test_research_report_combines_capture_execution_and_paper(tmp_path) -> None:
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
        max_book_age_ms=1_000,
        max_book_skew_ms=500,
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
    for timestamp, eth_bid in (
        (1_000, "60"),
        (1_050, "59"),
        (1_100, "58"),
        (1_200, "57"),
        (1_400, "56"),
    ):
        recorder.append_book(_book("BTCUSDT", "99", "100", timestamp))
        recorder.append_book(_book("ETHBTC", "0.49", "0.50", timestamp))
        recorder.append_book(_book("ETHUSDT", eth_bid, "61", timestamp))

    profiles = (
        LatencyProfile("fast", 25, 25, 150),
        LatencyProfile("slow", 100, 100, 300),
    )
    report = build_triangle_report(
        str(path),
        initial_balance=Decimal("500"),
        profiles=profiles,
        top_routes=4,
    )

    assert report.capture.snapshots == 15
    assert report.capture.p95_feed_delay_ms == 5
    assert report.approved_signals > 0
    assert [profile.profile_name for profile in report.profiles] == ["fast", "slow"]
    assert all(profile.detected_signals == report.approved_signals for profile in report.profiles)
    assert len(report.top_routes) <= 4
    payload = report_json(report)
    assert '"approved_signals"' in payload
    assert '"profile_name":"fast"' in payload
