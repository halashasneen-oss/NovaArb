from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import ArbitrageOpportunity, ZERO


class RiskReason(StrEnum):
    APPROVED = "approved"
    EDGE_TOO_SMALL = "edge_too_small"
    STALE_BOOK = "stale_book"
    NOTIONAL_TOO_LARGE = "notional_too_large"
    NON_POSITIVE_PROFIT = "non_positive_profit"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    min_net_edge_bps: Decimal = Decimal("2.0")
    max_book_age_ms: int = 750
    max_notional_usd: Decimal = Decimal("100")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: RiskReason


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def assess(self, opportunity: ArbitrageOpportunity) -> RiskDecision:
        if opportunity.costs.net_profit_usd <= ZERO:
            return RiskDecision(False, RiskReason.NON_POSITIVE_PROFIT)
        if opportunity.costs.net_edge_bps < self.limits.min_net_edge_bps:
            return RiskDecision(False, RiskReason.EDGE_TOO_SMALL)
        if max(opportunity.buy_book_age_ms, opportunity.sell_book_age_ms) > self.limits.max_book_age_ms:
            return RiskDecision(False, RiskReason.STALE_BOOK)
        if opportunity.reference_notional_usd > self.limits.max_notional_usd:
            return RiskDecision(False, RiskReason.NOTIONAL_TOO_LARGE)
        return RiskDecision(True, RiskReason.APPROVED)
