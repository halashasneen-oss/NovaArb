from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_scanner import CrossVenuePublicScanner, CrossVenueResearchEngine
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.venue import Instrument, NormalizedBook, VenueInstrument


class _Adapter:
    def __init__(self, venue: str) -> None:
        self.venue = venue


def _book(
    *,
    venue: str,
    bid: str,
    ask: str,
    received_ms: int = 1_000,
) -> NormalizedBook:
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    snapshot = OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )
    return NormalizedBook(
        instrument=instrument,
        venue_symbol="BTCUSDT",
        snapshot=snapshot,
    )


def _engine() -> CrossVenueResearchEngine:
    return CrossVenueResearchEngine(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("2"),
        ),
        costs=(
            VenueCostProfile("alpha", Decimal("1")),
            VenueCostProfile("beta", Decimal("1")),
        ),
        inventories=(
            VenueInventory("alpha", Decimal("1"), Decimal("200")),
            VenueInventory("beta", Decimal("2"), Decimal("200")),
        ),
    )


def test_research_engine_emits_after_two_venues_are_observed() -> None:
    engine = _engine()
    assert engine.process_book(
        _book(venue="alpha", bid="99", ask="100"),
        now_ms=1_100,
    ) == ()

    events = engine.process_book(
        _book(venue="beta", bid="102", ask="103"),
        now_ms=1_100,
    )
    assert len(events) == 1
    event = events[0]
    assert event.decision.approved is True
    assert event.opportunity.buy_venue == "alpha"
    assert event.opportunity.sell_venue == "beta"
    assert event.opportunity.net_profit_quote > 0


def test_public_scanner_requires_one_instrument_on_two_venues() -> None:
    engine = _engine()
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    scanner = CrossVenuePublicScanner(
        engine=engine,
        adapters=(_Adapter("alpha"), _Adapter("beta")),
        instruments=(
            VenueInstrument("alpha", "BTCUSDT", instrument),
            VenueInstrument("beta", "BTCUSDT", instrument),
        ),
    )
    assert scanner.instruments[0].instrument == instrument
