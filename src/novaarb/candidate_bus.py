from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from novaarb.allocator import AllocationConfig, AllocationReason, CapitalAwareAllocator, ResearchCandidate
from novaarb.cross_venue import CrossVenueOpportunity
from novaarb.funding import FundingCarryOpportunity


CROSS_VENUE_STRATEGY = "prefunded_cross_venue"
FUNDING_STRATEGY = "funding_carry_long_spot_short_perp"


@dataclass(frozen=True, slots=True)
class CandidateEnvelope:
    candidate: ResearchCandidate
    family: str
    payload: Any


@dataclass(frozen=True, slots=True)
class CandidateBusDecision:
    envelope: CandidateEnvelope
    selected: bool
    reason: AllocationReason


@dataclass(frozen=True, slots=True)
class CandidateBusResult:
    selected: tuple[CandidateEnvelope, ...]
    decisions: tuple[CandidateBusDecision, ...]
    allocated_capital_usdt: Decimal
    remaining_capital_usdt: Decimal


def _pair(base_asset: str, quote_asset: str) -> str:
    base = base_asset.upper()
    quote = quote_asset.upper()
    if not base or not quote or base == quote:
        raise ValueError("distinct base and quote assets are required")
    return f"{base}/{quote}"


def cross_venue_envelope(
    opportunity: CrossVenueOpportunity,
    *,
    sequence: int,
) -> CandidateEnvelope:
    pair = _pair(
        opportunity.instrument.base_asset,
        opportunity.instrument.quote_asset,
    )
    candidate = ResearchCandidate(
        opportunity_id=(
            f"cross:{opportunity.created_time_ms}:{sequence}:"
            f"{pair}:{opportunity.buy_venue}:{opportunity.sell_venue}"
        ),
        strategy=CROSS_VENUE_STRATEGY,
        capital_required_usdt=(
            opportunity.buy_quote_required + opportunity.sell_quote_proceeds
        ),
        expected_net_profit_usdt=opportunity.net_profit_quote,
        expected_edge_bps=opportunity.net_edge_bps,
        resource_keys=(
            f"spot:{opportunity.buy_venue}:{pair}",
            f"spot:{opportunity.sell_venue}:{pair}",
        ),
        observed_at_ms=opportunity.created_time_ms,
    )
    return CandidateEnvelope(candidate, CROSS_VENUE_STRATEGY, opportunity)


def funding_envelope(
    opportunity: FundingCarryOpportunity,
    *,
    base_asset: str,
    quote_asset: str,
    sequence: int,
    venue: str = "binance",
    capital_multiplier: Decimal = Decimal("2"),
) -> CandidateEnvelope:
    if capital_multiplier <= 0:
        raise ValueError("funding capital_multiplier must be positive")
    pair = _pair(base_asset, quote_asset)
    candidate = ResearchCandidate(
        opportunity_id=(
            f"funding:{opportunity.created_time_ms}:{sequence}:"
            f"{venue}:{pair}"
        ),
        strategy=FUNDING_STRATEGY,
        capital_required_usdt=(
            opportunity.reference_notional_usdt * capital_multiplier
        ),
        expected_net_profit_usdt=opportunity.expected_net_profit_usdt,
        expected_edge_bps=opportunity.expected_net_edge_bps,
        resource_keys=(
            f"spot:{venue}:{pair}",
            f"perpetual:{venue}:{pair}",
        ),
        observed_at_ms=opportunity.created_time_ms,
    )
    return CandidateEnvelope(candidate, FUNDING_STRATEGY, opportunity)


class ShadowCandidateBus:
    """Ranks heterogeneous research candidates through one capital/resource allocator.

    The bus only selects research opportunities. It does not submit orders and it does not
    convert projected funding carry into realized PnL. Strategy-specific execution/replay remains
    responsible for proving fills, holding periods and exits.
    """

    def __init__(self, config: AllocationConfig) -> None:
        self.allocator = CapitalAwareAllocator(config)

    def allocate(self, envelopes: tuple[CandidateEnvelope, ...]) -> CandidateBusResult:
        if len({item.candidate.opportunity_id for item in envelopes}) != len(envelopes):
            raise ValueError("candidate envelope opportunity ids must be unique")
        allocation = self.allocator.allocate(tuple(item.candidate for item in envelopes))
        by_id = {item.candidate.opportunity_id: item for item in envelopes}
        decisions = tuple(
            CandidateBusDecision(
                envelope=by_id[item.candidate.opportunity_id],
                selected=item.selected,
                reason=item.reason,
            )
            for item in allocation.decisions
        )
        selected = tuple(by_id[item.opportunity_id] for item in allocation.selected)
        return CandidateBusResult(
            selected=selected,
            decisions=decisions,
            allocated_capital_usdt=allocation.allocated_capital_usdt,
            remaining_capital_usdt=allocation.remaining_capital_usdt,
        )
