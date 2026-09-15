from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.execution import (
    BookTimeline,
    ExecutionFailure,
    LatencyProfile,
    SequentialTriangleSimulator,
)
from novaarb.symbols import SymbolRules
from novaarb.triangular import TriangleConfig, TriangleRoute, TriangularStrategy


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


def _setup():
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
        min_net_edge_bps=Decimal("0"),
    )
    strategy = TriangularStrategy(rules=rules, config=config)
    signal_books = {
        "BTCUSDT": _book("BTCUSDT", "99", "100", 1_000),
        "ETHBTC": _book("ETHBTC", "0.49", "0.50", 1_000),
        "ETHUSDT": _book("ETHUSDT", "60", "61", 1_000),
    }
    opportunity = strategy.evaluate_route(route, signal_books, now_ms=1_000)
    assert opportunity is not None
    return rules, opportunity


def test_sequential_execution_reprices_each_leg_and_measures_decay() -> None:
    rules, opportunity = _setup()
    timeline = BookTimeline(
        (
            _book("BTCUSDT", "99", "100", 1_050),
            _book("ETHBTC", "0.49", "0.50", 1_100),
            _book("ETHUSDT", "49", "50", 1_150),
        )
    )
    simulator = SequentialTriangleSimulator(
        rules=rules,
        taker_fee_bps=Decimal("0"),
        latency=LatencyProfile("100ms", 50, 50, 25),
    )

    result = simulator.simulate(opportunity, timeline)

    assert result.completed
    assert len(result.legs) == 3
    assert result.realized_edge_bps < result.detected_edge_bps
    assert result.edge_decay_bps > 0
    assert result.duration_ms == 150


def test_partial_sequence_reports_open_exposure() -> None:
    rules, opportunity = _setup()
    timeline = BookTimeline(
        (
            _book("BTCUSDT", "99", "100", 1_050),
            _book("ETHBTC", "0.49", "0.50", 1_100),
        )
    )
    simulator = SequentialTriangleSimulator(
        rules=rules,
        taker_fee_bps=Decimal("0"),
        latency=LatencyProfile("100ms", 50, 50, 25),
    )

    result = simulator.simulate(opportunity, timeline)

    assert not result.completed
    assert result.failure is ExecutionFailure.MISSING_FUTURE_BOOK
    assert result.failure_leg_index == 2
    assert result.exposure_asset == "ETH"
    assert result.exposure_amount > 0
    assert len(result.legs) == 2


def test_book_wait_limit_rejects_stale_future_snapshot() -> None:
    rules, opportunity = _setup()
    timeline = BookTimeline(
        (
            _book("BTCUSDT", "99", "100", 1_500),
            _book("ETHBTC", "0.49", "0.50", 1_550),
            _book("ETHUSDT", "60", "61", 1_600),
        )
    )
    simulator = SequentialTriangleSimulator(
        rules=rules,
        taker_fee_bps=Decimal("0"),
        latency=LatencyProfile("strict", 50, 50, 20),
    )

    result = simulator.simulate(opportunity, timeline)

    assert not result.completed
    assert result.failure is ExecutionFailure.BOOK_WAIT_EXCEEDED
    assert result.failure_leg_index == 0
