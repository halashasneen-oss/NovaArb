from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.execution import BookTimeline, LatencyProfile, SequentialTriangleSimulator
from novaarb.recovery import EmergencyUnwinder, RecoveryPolicy
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


def test_emergency_unwind_prices_partial_exposure_back_to_anchor() -> None:
    rules = (
        _rule("BTCUSDT", "BTC", "USDT"),
        _rule("ETHBTC", "ETH", "BTC"),
        _rule("ETHUSDT", "ETH", "USDT"),
    )
    route = TriangleRoute("USDT", "BTC", "ETH", ("BTCUSDT", "ETHBTC", "ETHUSDT"))
    strategy = TriangularStrategy(
        rules=rules,
        config=TriangleConfig(
            starting_amount=Decimal("100"),
            taker_fee_bps=Decimal("0"),
            execution_reserve_bps=Decimal("0"),
            min_net_edge_bps=Decimal("0"),
        ),
    )
    signal = {
        "BTCUSDT": _book("BTCUSDT", "99", "100", 1_000),
        "ETHBTC": _book("ETHBTC", "0.49", "0.50", 1_000),
        "ETHUSDT": _book("ETHUSDT", "60", "61", 1_000),
    }
    opportunity = strategy.evaluate_route(route, signal, now_ms=1_000)
    assert opportunity is not None

    timeline = BookTimeline(
        (
            _book("BTCUSDT", "99", "100", 1_050),
            _book("ETHBTC", "0.49", "0.50", 1_100),
            _book("ETHUSDT", "55", "56", 1_300),
        )
    )
    simulator = SequentialTriangleSimulator(
        rules=rules,
        taker_fee_bps=Decimal("0"),
        latency=LatencyProfile("strict", 50, 50, 25),
    )
    failed = simulator.simulate(opportunity, timeline)
    assert not failed.completed
    assert len(failed.legs) == 2
    assert failed.exposure_asset == "ETH"

    recovery = EmergencyUnwinder(
        rules=rules,
        taker_fee_bps=Decimal("0"),
        policy=RecoveryPolicy(delay_ms=100, max_book_wait_ms=100),
    ).recover(opportunity, failed, timeline)

    assert recovery.attempted
    assert recovery.recovered
    assert recovery.fill is not None
    assert recovery.exposure_asset == "ETH"
    assert recovery.anchor_amount > 0
