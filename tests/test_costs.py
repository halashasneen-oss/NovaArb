from decimal import Decimal

from novaarb.costs import ExecutableEdgeModel, FeeSchedule
from novaarb.domain import MarketType, Side
from novaarb.orderbook import simulate_base_fill
from conftest import make_book


def test_cost_decomposition_does_not_double_count_spread() -> None:
    buy = make_book(market=MarketType.SPOT, bid="99", ask="100")
    sell = make_book(market=MarketType.PERPETUAL, bid="102", ask="103")
    qty = Decimal("1")
    buy_fill = simulate_base_fill(buy, Side.BUY, qty)
    sell_fill = simulate_base_fill(sell, Side.SELL, qty)
    model = ExecutableEdgeModel(
        FeeSchedule(),
        latency_reserve_bps=Decimal("0"),
        exit_market_reserve_bps=Decimal("0"),
    )
    costs = model.evaluate(
        buy_book=buy,
        sell_book=sell,
        buy_fill=buy_fill,
        sell_fill=sell_fill,
        buy_fee_bps=Decimal("0"),
        sell_fee_bps=Decimal("0"),
    )
    assert costs.gross_dislocation_usd == Decimal("3")
    assert costs.entry_spread_cost_usd == Decimal("1")
    assert costs.depth_slippage_usd == Decimal("0")
    assert costs.net_capture_usd == Decimal("2")


def test_exit_reserve_prevents_false_instant_profit() -> None:
    buy = make_book(market=MarketType.SPOT, bid="99.95", ask="100.00")
    sell = make_book(market=MarketType.PERPETUAL, bid="100.20", ask="100.25")
    qty = Decimal("1")
    buy_fill = simulate_base_fill(buy, Side.BUY, qty)
    sell_fill = simulate_base_fill(sell, Side.SELL, qty)
    model = ExecutableEdgeModel(
        FeeSchedule(),
        latency_reserve_bps=Decimal("0"),
        exit_market_reserve_bps=Decimal("2"),
    )
    costs = model.evaluate(
        buy_book=buy,
        sell_book=sell,
        buy_fill=buy_fill,
        sell_fill=sell_fill,
        buy_fee_bps=Decimal("10"),
        sell_fee_bps=Decimal("5"),
    )
    assert costs.gross_dislocation_usd > 0
    assert costs.exit_reserve_usd > 0
    assert costs.net_capture_usd < 0
