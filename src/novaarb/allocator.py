from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import ZERO


class AllocationReason(StrEnum):
    SELECTED = "selected"
    NON_POSITIVE_PROFIT = "non_positive_profit"
    EDGE_TOO_SMALL = "edge_too_small"
    OPPORTUNITY_CAP_EXCEEDED = "opportunity_cap_exceeded"
    RESOURCE_CONFLICT = "resource_conflict"
    STRATEGY_POSITION_LIMIT = "strategy_position_limit"
    STRATEGY_CAPITAL_LIMIT = "strategy_capital_limit"
    PORTFOLIO_POSITION_LIMIT = "portfolio_position_limit"
    INSUFFICIENT_CAPITAL = "insufficient_capital"


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    opportunity_id: str
    strategy: str
    capital_required_usdt: Decimal
    expected_net_profit_usdt: Decimal
    expected_edge_bps: Decimal
    resource_keys: tuple[str, ...]
    observed_at_ms: int

    def __post_init__(self) -> None:
        if not self.opportunity_id:
            raise ValueError("opportunity_id is required")
        if not self.strategy:
            raise ValueError("strategy is required")
        if self.capital_required_usdt <= ZERO:
            raise ValueError("capital_required_usdt must be positive")
        if len(self.resource_keys) != len(set(self.resource_keys)):
            raise ValueError("resource_keys must be unique within a candidate")


@dataclass(frozen=True, slots=True)
class StrategyCapitalLimit:
    strategy: str
    max_positions: int
    max_capital_usdt: Decimal

    def __post_init__(self) -> None:
        if not self.strategy:
            raise ValueError("strategy is required")
        if self.max_positions <= 0:
            raise ValueError("max_positions must be positive")
        if self.max_capital_usdt <= ZERO:
            raise ValueError("max_capital_usdt must be positive")


@dataclass(frozen=True, slots=True)
class AllocationConfig:
    total_capital_usdt: Decimal
    cash_reserve_usdt: Decimal = Decimal("0")
    max_positions: int = 4
    max_capital_per_opportunity_usdt: Decimal = Decimal("100")
    min_expected_edge_bps: Decimal = Decimal("1")
    enforce_resource_exclusivity: bool = True
    strategy_limits: tuple[StrategyCapitalLimit, ...] = ()

    def __post_init__(self) -> None:
        if self.total_capital_usdt <= ZERO:
            raise ValueError("total_capital_usdt must be positive")
        if self.cash_reserve_usdt < ZERO or self.cash_reserve_usdt >= self.total_capital_usdt:
            raise ValueError("cash_reserve_usdt must be non-negative and below total capital")
        if self.max_positions <= 0:
            raise ValueError("max_positions must be positive")
        if self.max_capital_per_opportunity_usdt <= ZERO:
            raise ValueError("max_capital_per_opportunity_usdt must be positive")
        if self.min_expected_edge_bps < ZERO:
            raise ValueError("min_expected_edge_bps cannot be negative")
        names = [limit.strategy for limit in self.strategy_limits]
        if len(names) != len(set(names)):
            raise ValueError("strategy limits must have unique strategy names")

    @property
    def deployable_capital_usdt(self) -> Decimal:
        return self.total_capital_usdt - self.cash_reserve_usdt


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    candidate: ResearchCandidate
    selected: bool
    reason: AllocationReason
    allocated_capital_after_usdt: Decimal


@dataclass(frozen=True, slots=True)
class AllocationResult:
    selected: tuple[ResearchCandidate, ...]
    decisions: tuple[AllocationDecision, ...]
    allocated_capital_usdt: Decimal
    remaining_capital_usdt: Decimal


class CapitalAwareAllocator:
    """Greedy research allocator with explicit capital and resource constraints.

    Optional persistent-state arguments let a replay/live shadow coordinator account for capital,
    positions and market resources already occupied by longer-lived research positions. Existing
    callers that omit them retain the original batch-only behavior.
    """

    def __init__(self, config: AllocationConfig) -> None:
        self.config = config
        self.strategy_limits = {limit.strategy: limit for limit in config.strategy_limits}

    @staticmethod
    def _priority(candidate: ResearchCandidate) -> tuple[Decimal, Decimal, int, str]:
        return (
            candidate.expected_edge_bps,
            candidate.expected_net_profit_usdt,
            -candidate.observed_at_ms,
            candidate.opportunity_id,
        )

    def allocate(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        reserved_capital_usdt: Decimal = ZERO,
        open_positions: int = 0,
        used_resources: frozenset[str] = frozenset(),
        strategy_capital_usdt: dict[str, Decimal] | None = None,
        strategy_positions: dict[str, int] | None = None,
    ) -> AllocationResult:
        if len({candidate.opportunity_id for candidate in candidates}) != len(candidates):
            raise ValueError("candidate opportunity_id values must be unique")
        if reserved_capital_usdt < ZERO:
            raise ValueError("reserved_capital_usdt cannot be negative")
        if reserved_capital_usdt > self.config.deployable_capital_usdt:
            raise ValueError("reserved capital exceeds deployable capital")
        if open_positions < 0 or open_positions > self.config.max_positions:
            raise ValueError("open_positions must be within portfolio position limits")

        existing_strategy_capital = dict(strategy_capital_usdt or {})
        existing_strategy_positions = dict(strategy_positions or {})
        if any(value < ZERO for value in existing_strategy_capital.values()):
            raise ValueError("existing strategy capital cannot be negative")
        if any(value < 0 for value in existing_strategy_positions.values()):
            raise ValueError("existing strategy positions cannot be negative")

        ordered = sorted(candidates, key=self._priority, reverse=True)
        selected: list[ResearchCandidate] = []
        decisions: list[AllocationDecision] = []
        occupied_resources = set(used_resources)
        allocated = reserved_capital_usdt
        strategy_capital = existing_strategy_capital
        active_strategy_positions = existing_strategy_positions

        for candidate in ordered:
            reason = self._rejection_reason(
                candidate,
                selected_count=open_positions + len(selected),
                allocated=allocated,
                used_resources=occupied_resources,
                strategy_capital=strategy_capital,
                strategy_positions=active_strategy_positions,
            )
            if reason is not None:
                decisions.append(
                    AllocationDecision(
                        candidate=candidate,
                        selected=False,
                        reason=reason,
                        allocated_capital_after_usdt=allocated,
                    )
                )
                continue

            selected.append(candidate)
            allocated += candidate.capital_required_usdt
            occupied_resources.update(candidate.resource_keys)
            strategy_capital[candidate.strategy] = (
                strategy_capital.get(candidate.strategy, ZERO)
                + candidate.capital_required_usdt
            )
            active_strategy_positions[candidate.strategy] = (
                active_strategy_positions.get(candidate.strategy, 0) + 1
            )
            decisions.append(
                AllocationDecision(
                    candidate=candidate,
                    selected=True,
                    reason=AllocationReason.SELECTED,
                    allocated_capital_after_usdt=allocated,
                )
            )

        return AllocationResult(
            selected=tuple(selected),
            decisions=tuple(decisions),
            allocated_capital_usdt=allocated,
            remaining_capital_usdt=self.config.deployable_capital_usdt - allocated,
        )

    def _rejection_reason(
        self,
        candidate: ResearchCandidate,
        *,
        selected_count: int,
        allocated: Decimal,
        used_resources: set[str],
        strategy_capital: dict[str, Decimal],
        strategy_positions: dict[str, int],
    ) -> AllocationReason | None:
        if candidate.expected_net_profit_usdt <= ZERO:
            return AllocationReason.NON_POSITIVE_PROFIT
        if candidate.expected_edge_bps < self.config.min_expected_edge_bps:
            return AllocationReason.EDGE_TOO_SMALL
        if candidate.capital_required_usdt > self.config.max_capital_per_opportunity_usdt:
            return AllocationReason.OPPORTUNITY_CAP_EXCEEDED
        if (
            self.config.enforce_resource_exclusivity
            and set(candidate.resource_keys) & used_resources
        ):
            return AllocationReason.RESOURCE_CONFLICT
        if selected_count >= self.config.max_positions:
            return AllocationReason.PORTFOLIO_POSITION_LIMIT

        strategy_limit = self.strategy_limits.get(candidate.strategy)
        if strategy_limit is not None:
            if strategy_positions.get(candidate.strategy, 0) >= strategy_limit.max_positions:
                return AllocationReason.STRATEGY_POSITION_LIMIT
            if (
                strategy_capital.get(candidate.strategy, ZERO)
                + candidate.capital_required_usdt
                > strategy_limit.max_capital_usdt
            ):
                return AllocationReason.STRATEGY_CAPITAL_LIMIT

        if allocated + candidate.capital_required_usdt > self.config.deployable_capital_usdt:
            return AllocationReason.INSUFFICIENT_CAPITAL
        return None
