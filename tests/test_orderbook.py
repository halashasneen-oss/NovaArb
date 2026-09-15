from decimal import Decimal

import pytest

from novaarb.domain import MarketType, Side
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill
from conftest import make_book


def test_buy_fill_walks_depth() -> None:
    book = make_book(
        market=MarketType.SPOT,
        bid="99",
        ask="100",
        ask_qty="1",
    )
    fill = simulate_base_fill(book, Side.BUY, Decimal("2"))
    assert fill.quote_quantity == Decimal("201")
    assert fill.average_price == Decimal("100.5")
    assert fill.depth_slippage_usd == Decimal("1")
    assert fill.levels_used == 2


def test_insufficient_depth_is_rejected() -> None:
    book = make_book(market=MarketType.SPOT, bid="99", ask="100")
    with pytest.raises(InsufficientLiquidity):
        simulate_base_fill(book, Side.BUY, Decimal("100"))
