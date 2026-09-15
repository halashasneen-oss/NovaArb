from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder
from novaarb.symbols import SymbolRules
from novaarb.triangle_replay import replay_triangle_log
from novaarb.triangular import TriangleConfig, TrianglePlanner


def _book(symbol: str, bid: str, ask: str, ts: int) -> OrderBookSnapshot:
    bid_d = Decimal(bid)
    ask_d = Decimal(ask)
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(bid_d, Decimal("100000")),),
        asks=(BookLevel(ask_d, Decimal("100000")),),
        event_time_ms=ts,
        received_time_ms=ts,
    )


def _rules(symbol: str, base: str, quote: str) -> SymbolRules:
    return SymbolRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        status="TRADING",
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.000001"),
        min_notional=Decimal("1"),
    )


def test_triangle_log_is_self_contained_and_replayable(tmp_path) -> None:
    rules = (
        _rules("BTCUSDT", "BTC", "USDT"),
        _rules("ETHBTC", "ETH", "BTC"),
        _rules("ETHUSDT", "ETH", "USDT"),
    )
    routes = TrianglePlanner(rules).routes(
        anchor_asset="USDT",
        allowed_assets={"USDT", "BTC", "ETH"},
    )
    config = TriangleConfig(
        starting_amount=Decimal("100"),
        taker_fee_bps=Decimal("0"),
        execution_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("1"),
        max_book_age_ms=500,
        max_book_skew_ms=100,
    )
    path = tmp_path / "triangle.jsonl.gz"
    recorder = ResearchRecorder(path)
    recorder.append_metadata(
        "triangle_session",
        {
            "anchor": "USDT",
            "allowed_assets": ["BTC", "ETH", "USDT"],
            "config": config,
            "rules": rules,
            "routes": routes,
        },
    )
    recorder.append_book(_book("BTCUSDT", "9.99", "10.00", ts=1_000))
    recorder.append_book(_book("ETHBTC", "0.499", "0.500", ts=1_010))
    recorder.append_book(_book("ETHUSDT", "5.10", "5.11", ts=1_020))

    summary = replay_triangle_log(str(path))
    assert summary.snapshots == 3
    assert summary.evaluations >= 1
    assert summary.approved_observations >= 1
    assert summary.max_edge_bps > 0
    assert any(
        route.route_id == "USDT>BTC>ETH>USDT"
        for route in summary.top_routes
    )
