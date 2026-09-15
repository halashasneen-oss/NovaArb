from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.shadow_portfolio_cli import _parse_balances, _parse_instruments


def test_parse_instruments_maps_binance_and_bybit() -> None:
    mappings = _parse_instruments(["btc/usdt", "ETH/USDT"])

    assert len(mappings) == 4
    assert {item.venue for item in mappings} == {"binance", "bybit"}
    assert {item.instrument.canonical_symbol for item in mappings} == {
        "BTC/USDT:spot",
        "ETH/USDT:spot",
    }
    assert {item.venue_symbol for item in mappings} == {"BTCUSDT", "ETHUSDT"}


def test_parse_balances_normalizes_venue_and_asset() -> None:
    balances = _parse_balances(
        [
            "Binance:usdt:1000",
            "bybit:BTC:0.25",
        ]
    )

    assert balances[0].venue == "binance"
    assert balances[0].asset == "USDT"
    assert balances[0].quantity == Decimal("1000")
    assert balances[1].venue == "bybit"
    assert balances[1].asset == "BTC"


def test_parse_inputs_reject_duplicates_and_bad_shapes() -> None:
    with pytest.raises(ValueError, match="duplicate instrument"):
        _parse_instruments(["BTC/USDT", "btc/usdt"])
    with pytest.raises(ValueError, match="expected BASE/QUOTE"):
        _parse_instruments(["BTCUSDT"])
    with pytest.raises(ValueError, match="duplicate balance"):
        _parse_balances(["binance:USDT:1", "Binance:usdt:2"])
    with pytest.raises(ValueError, match="expected VENUE:ASSET:QUANTITY"):
        _parse_balances(["binance:USDT"])
