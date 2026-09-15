from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from novaarb.domain import ArbitrageOpportunity, ZERO


@dataclass(frozen=True, slots=True)
class PaperTrade:
    sequence: int
    strategy: str
    symbol: str
    notional_usd: Decimal
    net_profit_usd: Decimal
    net_edge_bps: Decimal
    created_time_ms: int


@dataclass(slots=True)
class PaperPortfolio:
    starting_cash_usd: Decimal = Decimal("1000")
    realized_pnl_usd: Decimal = ZERO
    trades: list[PaperTrade] = field(default_factory=list)

    @property
    def equity_usd(self) -> Decimal:
        return self.starting_cash_usd + self.realized_pnl_usd

    def record(self, opportunity: ArbitrageOpportunity) -> PaperTrade:
        trade = PaperTrade(
            sequence=len(self.trades) + 1,
            strategy=opportunity.strategy,
            symbol=opportunity.symbol,
            notional_usd=opportunity.reference_notional_usd,
            net_profit_usd=opportunity.costs.net_profit_usd,
            net_edge_bps=opportunity.costs.net_edge_bps,
            created_time_ms=opportunity.created_time_ms,
        )
        self.realized_pnl_usd += trade.net_profit_usd
        self.trades.append(trade)
        return trade
