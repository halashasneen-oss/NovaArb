from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.symbols import SymbolRules
from novaarb.triangular import TriangleConfig, TrianglePlanner, TriangularStrategy


def _rules(symbol: str, base: str, quote: str, step: str = "0.000001") -> SymbolRules:
    return SymbolRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        status="TRADING",
        step_size=Decimal(step),
        min_quantity=Decimal(step),
        min_notional=Decimal("1"),
    )


def _book(symbol: str, bid: str, ask: str, ts: int = 1_000) -> OrderBookSnapshot:
    bid_d = Decimal(bid)
    ask_d = Decimal(ask)
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(
            BookLevel(bid_d, Decimal("100000")),
            BookLevel(bid_d * Decimal("0.999"), Decimal("100000")),
        ),
        asks=(
            BookLevel(ask_d, Decimal("100000")),
            BookLevel(ask_d * Decimal("1.001"), Decimal("100000")),
        ),
        event_time_ms=ts,
        received_time_ms=ts,
    )


def test_planner_finds_both_triangle_directions() -> None:
    rules = (
        _rules("BTCUSDT", "BTC", "USDT"),
        _rules("ETHBTC", "ETH", "BTC"),
        _rules("ETHUSDT", "ETH", "USDT"),
    )
    routes = TrianglePlanner(rules).routes(
        anchor_asset="USDT",
        allowed_assets={"USDT", "BTC", "ETH"},
    )
    assert {route.route_id for route in routes} == {
        "USDT>BTC>ETH>USDT",
        "USDT>ETH>BTC>USDT",
    }


def test_profitable_triangle_uses_executable_bid_ask_path() -> None:
    rules = (
        _rules("BTCUSDT", "BTC", "USDT"),
        _rules("ETHBTC", "ETH", "BTC"),
        _rules("ETHUSDT", "ETH", "USDT"),
    )
    route = next(
        route
        for route in TrianglePlanner(rules).routes(
            anchor_asset="USDT",
            allowed_assets={"USDT", "BTC", "ETH"},
        )
        if route.route_id == "USDT>BTC>ETH>USDT"
    )
    books = {
        "BTCUSDT": _book("BTCUSDT", "9.99", "10.00"),
        "ETHBTC": _book("ETHBTC", "0.499", "0.500"),
        "ETHUSDT": _book("ETHUSDT", "5.10", "5.11"),
    }
    strategy = TriangularStrategy(
        rules=rules,
        config=TriangleConfig(
            starting_amount=Decimal("100"),
            taker_fee_bps=Decimal("0"),
            execution_reserve_bps=Decimal("0"),
            min_net_edge_bps=Decimal("1"),
        ),
    )
    opportunity = strategy.evaluate_route(route, books, now_ms=1_050)
    assert opportunity is not None
    assert opportunity.final_amount_before_reserve == Decimal("102.0000000")
    assert opportunity.net_profit == Decimal("2.0000000")
    assert strategy.assess(opportunity).approved is True


def test_fees_and_execution_reserve_can_remove_triangle_edge() -> None:
    rules = (
        _rules("BTCUSDT", "BTC", "USDT"),
        _rules("ETHBTC", "ETH", "BTC"),
        _rules("ETHUSDT", "ETH", "USDT"),
    )
    route = next(
        route
        for route in TrianglePlanner(rules).routes(
            anchor_asset="USDT",
            allowed_assets={"USDT", "BTC", "ETH"},
        )
        if route.route_id == "USDT>BTC>ETH>USDT"
    )
    books = {
        "BTCUSDT": _book("BTCUSDT", "9.99", "10.00"),
        "ETHBTC": _book("ETHBTC", "0.499", "0.500"),
        "ETHUSDT": _book("ETHUSDT", "5.01", "5.02"),
    }
    strategy = TriangularStrategy(
        rules=rules,
        config=TriangleConfig(
            starting_amount=Decimal("100"),
            taker_fee_bps=Decimal("10"),
            execution_reserve_bps=Decimal("5"),
            min_net_edge_bps=Decimal("1"),
        ),
    )
    opportunity = strategy.evaluate_route(route, books, now_ms=1_050)
    assert opportunity is not None
    assert strategy.assess(opportunity).approved is False


def test_triangle_rejects_cross_book_time_skew() -> None:
    rules = (
        _rules("BTCUSDT", "BTC", "USDT"),
        _rules("ETHBTC", "ETH", "BTC"),
        _rules("ETHUSDT", "ETH", "USDT"),
    )
    route = next(
        route
        for route in TrianglePlanner(rules).routes(
            anchor_asset="USDT",
            allowed_assets={"USDT", "BTC", "ETH"},
        )
        if route.route_id == "USDT>BTC>ETH>USDT"
    )
    books = {
        "BTCUSDT": _book("BTCUSDT", "9.99", "10.00", ts=1_000),
        "ETHBTC": _book("ETHBTC", "0.499", "0.500", ts=1_050),
        "ETHUSDT": _book("ETHUSDT", "5.10", "5.11", ts=1_350),
    }
    strategy = TriangularStrategy(
        rules=rules,
        config=TriangleConfig(
            starting_amount=Decimal("100"),
            taker_fee_bps=Decimal("0"),
            execution_reserve_bps=Decimal("0"),
            min_net_edge_bps=Decimal("1"),
            max_book_age_ms=500,
            max_book_skew_ms=100,
        ),
    )
    opportunity = strategy.evaluate_route(route, books, now_ms=1_400)
    assert opportunity is not None
    assert strategy.assess(opportunity).reason.value == "book_skew"
