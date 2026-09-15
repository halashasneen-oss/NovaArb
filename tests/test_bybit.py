from __future__ import annotations

from decimal import Decimal

from novaarb.bybit import BYBIT_LINEAR_WS, BYBIT_SPOT_WS, BybitDepthStream, BybitOrderBookState
from novaarb.domain import MarketType


def test_bybit_depth_stream_selects_public_endpoint() -> None:
    spot = BybitDepthStream(symbols=("BTCUSDT",), market=MarketType.SPOT)
    perpetual = BybitDepthStream(symbols=("BTCUSDT",), market=MarketType.PERPETUAL)
    assert spot.url == BYBIT_SPOT_WS
    assert perpetual.url == BYBIT_LINEAR_WS


def test_bybit_state_builds_snapshot_and_applies_delta() -> None:
    state = BybitOrderBookState(
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        depth=50,
    )
    snapshot = state.apply(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1_000,
            "data": {
                "s": "BTCUSDT",
                "u": 10,
                "b": [["100", "2"], ["99", "3"]],
                "a": [["101", "2"], ["102", "3"]],
            },
        },
        received_time_ms=1_010,
    )
    assert snapshot is not None
    assert snapshot.venue == "bybit"
    assert snapshot.best_bid.price == Decimal("100")
    assert snapshot.best_ask.price == Decimal("101")
    assert snapshot.event_time_ms == 1_000

    updated = state.apply(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 1_020,
            "data": {
                "s": "BTCUSDT",
                "u": 11,
                "b": [["100", "0"], ["100.5", "1"]],
                "a": [["101", "1.5"]],
            },
        },
        received_time_ms=1_025,
    )
    assert updated is not None
    assert updated.best_bid.price == Decimal("100.5")
    assert updated.best_ask.quantity == Decimal("1.5")
    assert tuple(level.price for level in updated.bids) == (
        Decimal("100.5"),
        Decimal("99"),
    )


def test_bybit_update_id_one_resets_local_book() -> None:
    state = BybitOrderBookState(
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        depth=50,
    )
    first = state.apply(
        {
            "type": "snapshot",
            "ts": 1_000,
            "data": {
                "s": "BTCUSDT",
                "u": 10,
                "b": [["100", "2"], ["99", "3"]],
                "a": [["101", "2"], ["102", "3"]],
            },
        },
        received_time_ms=1_000,
    )
    assert first is not None

    reset = state.apply(
        {
            "type": "delta",
            "ts": 2_000,
            "data": {
                "s": "BTCUSDT",
                "u": 1,
                "b": [["200", "1"]],
                "a": [["201", "1"]],
            },
        },
        received_time_ms=2_000,
    )
    assert reset is not None
    assert tuple(level.price for level in reset.bids) == (Decimal("200"),)
    assert tuple(level.price for level in reset.asks) == (Decimal("201"),)


def test_bybit_state_ignores_other_symbols_and_incomplete_books() -> None:
    state = BybitOrderBookState(
        symbol="BTCUSDT",
        market=MarketType.SPOT,
    )
    assert (
        state.apply(
            {
                "type": "snapshot",
                "data": {
                    "s": "ETHUSDT",
                    "u": 1,
                    "b": [["100", "1"]],
                    "a": [["101", "1"]],
                },
            },
            received_time_ms=1_000,
        )
        is None
    )
    assert (
        state.apply(
            {
                "type": "snapshot",
                "data": {
                    "s": "BTCUSDT",
                    "u": 1,
                    "b": [["100", "1"]],
                    "a": [],
                },
            },
            received_time_ms=1_000,
        )
        is None
    )
