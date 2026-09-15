from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from novaarb.domain import (
    TEN_THOUSAND,
    BookFill,
    CostBreakdown,
    OrderBookSnapshot,
    ZERO,
)


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    spot_taker_bps: Decimal = Decimal("10")
    futures_taker_bps: Decimal = Decimal("5")


class ExecutableEdgeModel:
    """Decomposes edge without double-counting spread or depth slippage."""

    def __init__(
        self,
        fees: FeeSchedule,
        latency_reserve_bps: Decimal = Decimal("0.75"),
    ) -> None:
        if latency_reserve_bps < ZERO:
            raise ValueError("latency reserve cannot be negative")
        self.fees = fees
        self.latency_reserve_bps = latency_reserve_bps

    def evaluate(
        self,
        *,
        buy_book: OrderBookSnapshot,
        sell_book: OrderBookSnapshot,
        buy_fill: BookFill,
        sell_fill: BookFill,
        buy_fee_bps: Decimal,
        sell_fee_bps: Decimal,
        funding_reserve_usd: Decimal = ZERO,
    ) -> CostBreakdown:
        quantity = buy_fill.base_quantity
        if quantity != sell_fill.base_quantity:
            raise ValueError("arbitrage legs must use the same base quantity")
        if buy_fee_bps < ZERO or sell_fee_bps < ZERO:
            raise ValueError("fee rates cannot be negative")
        if funding_reserve_usd < ZERO:
            raise ValueError("funding reserve cannot be negative")

        mid_dislocation = (sell_book.mid_price - buy_book.mid_price) * quantity
        spread_cost = (
            (buy_book.best_ask.price - buy_book.mid_price) * quantity
            + (sell_book.mid_price - sell_book.best_bid.price) * quantity
        )
        depth_slippage = buy_fill.depth_slippage_usd + sell_fill.depth_slippage_usd
        fees = (
            buy_fill.quote_quantity * buy_fee_bps / TEN_THOUSAND
            + sell_fill.quote_quantity * sell_fee_bps / TEN_THOUSAND
        )
        reference_notional = (
            buy_fill.quote_quantity + sell_fill.quote_quantity
        ) / Decimal("2")
        latency_reserve = reference_notional * self.latency_reserve_bps / TEN_THOUSAND

        net = (
            mid_dislocation
            - spread_cost
            - depth_slippage
            - fees
            - latency_reserve
            - funding_reserve_usd
        )
        net_edge_bps = ZERO
        if reference_notional > ZERO:
            net_edge_bps = net / reference_notional * TEN_THOUSAND

        return CostBreakdown(
            mid_dislocation_usd=mid_dislocation,
            spread_cost_usd=max(ZERO, spread_cost),
            depth_slippage_usd=depth_slippage,
            fees_usd=fees,
            latency_reserve_usd=latency_reserve,
            funding_reserve_usd=funding_reserve_usd,
            net_profit_usd=net,
            net_edge_bps=net_edge_bps,
        )
