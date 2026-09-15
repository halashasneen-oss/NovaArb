from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.venue import Instrument, NormalizedBook, VenueInstrument


def _book(symbol: str, market: MarketType) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=market,
        bids=(BookLevel(Decimal("99"), Decimal("10")),),
        asks=(BookLevel(Decimal("100"), Decimal("10")),),
        event_time_ms=1_000,
        received_time_ms=1_000,
    )


def test_instrument_normalizes_assets_and_symbol() -> None:
    instrument = Instrument("btc", "usdt", MarketType.SPOT)
    assert instrument.base_asset == "BTC"
    assert instrument.quote_asset == "USDT"
    assert instrument.canonical_symbol == "BTC/USDT:spot"


def test_normalized_book_enforces_mapping() -> None:
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    mapped = NormalizedBook(
        instrument=instrument,
        venue_symbol="BTCUSDT",
        snapshot=_book("BTCUSDT", MarketType.SPOT),
    )
    assert mapped.venue == "binance"

    with pytest.raises(ValueError, match="market"):
        NormalizedBook(
            instrument=instrument,
            venue_symbol="BTCUSDT",
            snapshot=_book("BTCUSDT", MarketType.PERPETUAL),
        )

    with pytest.raises(ValueError, match="symbol"):
        NormalizedBook(
            instrument=instrument,
            venue_symbol="ETHUSDT",
            snapshot=_book("BTCUSDT", MarketType.SPOT),
        )


def test_venue_instrument_requires_identity() -> None:
    instrument = Instrument("BTC", "USDT", MarketType.SPOT)
    item = VenueInstrument("binance", "BTCUSDT", instrument)
    assert item.instrument == instrument
    with pytest.raises(ValueError):
        VenueInstrument("", "BTCUSDT", instrument)
