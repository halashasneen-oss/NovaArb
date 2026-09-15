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
    model = ExecutableEdgeModel(FeeSchedule(), latency_reserve_bps=Decimal("0"))
    costs = model.evaluate(
        buy_book=buy,
        sell_book=sell,
        buy_fill=buy_fill,
        sell_fill=sell_fill,
        buy_fee_bps=Decimal("0"),
        sell_fee_bps=Decimal("0"),
    )
    assert costs.mid_dislocation_usd == Decimal("3")
    assert costs.spread_cost_usd == Decimal("1")
    assert costs.depth_slippage_usd == Decimal("0")
    assert costs.net_profit_usd == Decimal("2")
