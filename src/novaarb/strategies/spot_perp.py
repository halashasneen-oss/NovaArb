from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from novaarb.costs import ExecutableEdgeModel, FeeSchedule
from novaarb.domain import ArbitrageOpportunity, MarketType, OrderBookSnapshot, Side
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill


@dataclass(frozen=True, slots=True)
class SpotPerpConfig:
    target_notional_usd: Decimal = Decimal("50")
    allow_spot_short: bool = False


class SpotPerpStrategy:
    name = "spot_perp"

    def __init__(
        self,
        *,
        config: SpotPerpConfig,
        fees: FeeSchedule,
        edge_model: ExecutableEdgeModel,
    ) -> None:
        if config.target_notional_usd <= 0:
            raise ValueError("target notional must be positive")
        self.config = config
        self.fees = fees
        self.edge_model = edge_model

    def evaluate(
        self,
        spot: OrderBookSnapshot,
        perpetual: OrderBookSnapshot,
        *,
        now_ms: int | None = None,
    ) -> tuple[ArbitrageOpportunity, ...]:
        self._validate_pair(spot, perpetual)
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        candidates: list[ArbitrageOpportunity] = []

        cash_and_carry = self._build(
            buy_book=spot,
            sell_book=perpetual,
            buy_fee_bps=self.fees.spot_taker_bps,
            sell_fee_bps=self.fees.futures_taker_bps,
            now_ms=now_ms,
        )
        if cash_and_carry is not None:
            candidates.append(cash_and_carry)

        if self.config.allow_spot_short:
            reverse = self._build(
                buy_book=perpetual,
                sell_book=spot,
                buy_fee_bps=self.fees.futures_taker_bps,
                sell_fee_bps=self.fees.spot_taker_bps,
                now_ms=now_ms,
            )
            if reverse is not None:
                candidates.append(reverse)

        return tuple(candidates)

    def _build(
        self,
        *,
        buy_book: OrderBookSnapshot,
        sell_book: OrderBookSnapshot,
        buy_fee_bps: Decimal,
        sell_fee_bps: Decimal,
        now_ms: int,
    ) -> ArbitrageOpportunity | None:
        reference_price = max(buy_book.best_ask.price, sell_book.best_bid.price)
        base_quantity = self.config.target_notional_usd / reference_price
        try:
            buy_fill = simulate_base_fill(buy_book, Side.BUY, base_quantity)
            sell_fill = simulate_base_fill(sell_book, Side.SELL, base_quantity)
        except InsufficientLiquidity:
            return None

        costs = self.edge_model.evaluate(
            buy_book=buy_book,
            sell_book=sell_book,
            buy_fill=buy_fill,
            sell_fill=sell_fill,
            buy_fee_bps=buy_fee_bps,
            sell_fee_bps=sell_fee_bps,
        )
        return ArbitrageOpportunity(
            strategy=self.name,
            venue=buy_book.venue,
            symbol=buy_book.symbol,
            buy_market=buy_book.market,
            sell_market=sell_book.market,
            base_quantity=base_quantity,
            buy_fill=buy_fill,
            sell_fill=sell_fill,
            costs=costs,
            created_time_ms=now_ms,
            buy_book_age_ms=buy_book.age_ms(now_ms),
            sell_book_age_ms=sell_book.age_ms(now_ms),
        )

    @staticmethod
    def _validate_pair(spot: OrderBookSnapshot, perpetual: OrderBookSnapshot) -> None:
        if spot.market is not MarketType.SPOT:
            raise ValueError("first book must be spot")
        if perpetual.market is not MarketType.PERPETUAL:
            raise ValueError("second book must be perpetual")
        if spot.venue != perpetual.venue or spot.symbol != perpetual.symbol:
            raise ValueError("spot/perpetual books must match venue and symbol")
