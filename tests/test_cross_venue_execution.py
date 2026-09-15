from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, CrossVenueStrategy, VenueCostProfile
from novaarb.cross_venue_execution import reprice_committed_cross_venue
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.venue import Instrument, NormalizedBook


def _book(
    *,
    venue: str,
    bid: str,
    ask: str,
    received_ms: int,
    quantity: str = "10",
) -> NormalizedBook:
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    snapshot = OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal(quantity)),),
        asks=(BookLevel(Decimal(ask), Decimal(quantity)),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )
    return NormalizedBook(
        instrument=instrument,
        venue_symbol="BTCUSDT",
        snapshot=snapshot,
    )


def _costs() -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile("alpha", Decimal("1")),
        VenueCostProfile("beta", Decimal("1")),
    )


def _detected():
    strategy = CrossVenueStrategy(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
        ),
        costs=_costs(),
    )
    detected = strategy.evaluate_direction(
        buy_book=_book(venue="alpha", bid="99", ask="100", received_ms=1_000),
        sell_book=_book(venue="beta", bid="102", ask="103", received_ms=1_000),
        now_ms=1_000,
    )
    assert detected is not None
    assert detected.net_profit_quote > 0
    return detected


def test_reprice_committed_quantity_keeps_adverse_fill_in_result() -> None:
    detected = _detected()

    realized = reprice_committed_cross_venue(
        detected,
        buy_book=_book(venue="alpha", bid="102", ask="103", received_ms=1_050),
        sell_book=_book(venue="beta", bid="101", ask="102", received_ms=1_052),
        costs=_costs(),
        now_ms=1_052,
    )

    assert realized is not None
    assert realized.base_quantity == detected.base_quantity
    assert realized.net_profit_quote < 0
    assert realized.net_edge_bps < 0
    assert realized.book_skew_ms == 2


def test_reprice_committed_quantity_fails_when_later_depth_cannot_fill() -> None:
    detected = _detected()

    realized = reprice_committed_cross_venue(
        detected,
        buy_book=_book(
            venue="alpha",
            bid="102",
            ask="103",
            received_ms=1_050,
            quantity="0.01",
        ),
        sell_book=_book(venue="beta", bid="101", ask="102", received_ms=1_050),
        costs=_costs(),
        now_ms=1_050,
    )

    assert realized is None
