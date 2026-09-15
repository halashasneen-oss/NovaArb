from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot


def make_book(
    *,
    market: MarketType,
    bid: str,
    ask: str,
    bid_qty: str = "10",
    ask_qty: str = "10",
    received_ms: int = 1_000,
) -> OrderBookSnapshot:
    bid_d = Decimal(bid)
    ask_d = Decimal(ask)
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=market,
        bids=(
            BookLevel(bid_d, Decimal(bid_qty)),
            BookLevel(bid_d - Decimal("1"), Decimal("10")),
        ),
        asks=(
            BookLevel(ask_d, Decimal(ask_qty)),
            BookLevel(ask_d + Decimal("1"), Decimal("10")),
        ),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )
