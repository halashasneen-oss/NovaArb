from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueOpportunity, VenueCostProfile
from novaarb.domain import TEN_THOUSAND, Side
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill
from novaarb.venue import NormalizedBook


def reprice_committed_cross_venue(
    detected: CrossVenueOpportunity,
    *,
    buy_book: NormalizedBook,
    sell_book: NormalizedBook,
    costs: tuple[VenueCostProfile, ...],
    now_ms: int,
) -> CrossVenueOpportunity | None:
    """Reprice the detected fixed base quantity on later observed books.

    This intentionally does not require the later edge to remain positive. Once a
    research candidate is treated as committed, adverse movement must remain in
    the simulated outcome instead of being silently converted into a rejection.
    """

    if buy_book.venue != detected.buy_venue or sell_book.venue != detected.sell_venue:
        raise ValueError("later books must preserve the detected buy/sell venues")
    if buy_book.instrument != detected.instrument or sell_book.instrument != detected.instrument:
        raise ValueError("later books must preserve the detected instrument")
    by_venue = {item.venue: item for item in costs}
    if len(by_venue) != len(costs):
        raise ValueError("venue cost profiles must be unique")
    buy_cost = by_venue.get(detected.buy_venue)
    sell_cost = by_venue.get(detected.sell_venue)
    if buy_cost is None or sell_cost is None:
        raise ValueError("missing venue cost profile for committed execution")

    try:
        buy_fill = simulate_base_fill(
            buy_book.snapshot,
            Side.BUY,
            detected.base_quantity,
        )
        sell_fill = simulate_base_fill(
            sell_book.snapshot,
            Side.SELL,
            detected.base_quantity,
        )
    except InsufficientLiquidity:
        return None

    buy_quote = buy_fill.quote_quantity
    sell_quote = sell_fill.quote_quantity
    gross_spread = sell_quote - buy_quote
    fee_cost = (
        buy_quote * buy_cost.taker_fee_bps / TEN_THOUSAND
        + sell_quote * sell_cost.taker_fee_bps / TEN_THOUSAND
    )
    reference_notional = (buy_quote + sell_quote) / Decimal("2")
    execution_reserve = (
        reference_notional
        * (buy_cost.execution_reserve_bps + sell_cost.execution_reserve_bps)
        / TEN_THOUSAND
    )
    rebalance_reserve = (
        reference_notional
        * (buy_cost.rebalance_reserve_bps + sell_cost.rebalance_reserve_bps)
        / TEN_THOUSAND
    )
    net_profit = gross_spread - fee_cost - execution_reserve - rebalance_reserve
    net_edge = net_profit / reference_notional * TEN_THOUSAND
    buy_age = buy_book.snapshot.age_ms(now_ms)
    sell_age = sell_book.snapshot.age_ms(now_ms)
    skew = abs(
        buy_book.snapshot.received_time_ms - sell_book.snapshot.received_time_ms
    )
    return CrossVenueOpportunity(
        instrument=detected.instrument,
        buy_venue=detected.buy_venue,
        sell_venue=detected.sell_venue,
        base_quantity=detected.base_quantity,
        buy_average_price=buy_fill.average_price,
        sell_average_price=sell_fill.average_price,
        buy_quote_required=buy_quote,
        sell_quote_proceeds=sell_quote,
        gross_spread_quote=gross_spread,
        fee_cost_quote=fee_cost,
        execution_reserve_quote=execution_reserve,
        rebalance_reserve_quote=rebalance_reserve,
        net_profit_quote=net_profit,
        net_edge_bps=net_edge,
        created_time_ms=now_ms,
        buy_book_age_ms=buy_age,
        sell_book_age_ms=sell_age,
        book_skew_ms=skew,
    )
