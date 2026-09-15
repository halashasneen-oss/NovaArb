from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.allocator import (
    AllocationConfig,
    AllocationReason,
    CapitalAwareAllocator,
    ResearchCandidate,
    StrategyCapitalLimit,
)


def _candidate(
    opportunity_id: str,
    *,
    strategy: str = "triangle",
    capital: str = "40",
    profit: str = "1",
    edge: str = "10",
    resources: tuple[str, ...] = (),
    observed_at_ms: int = 1_000,
) -> ResearchCandidate:
    return ResearchCandidate(
        opportunity_id=opportunity_id,
        strategy=strategy,
        capital_required_usdt=Decimal(capital),
        expected_net_profit_usdt=Decimal(profit),
        expected_edge_bps=Decimal(edge),
        resource_keys=resources,
        observed_at_ms=observed_at_ms,
    )


def test_allocator_prioritizes_edge_and_respects_capital_reserve() -> None:
    allocator = CapitalAwareAllocator(
        AllocationConfig(
            total_capital_usdt=Decimal("200"),
            cash_reserve_usdt=Decimal("50"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("100"),
            min_expected_edge_bps=Decimal("2"),
        )
    )
    result = allocator.allocate(
        (
            _candidate("a", capital="80", edge="9", profit="2"),
            _candidate("b", capital="80", edge="12", profit="1"),
            _candidate("c", capital="60", edge="8", profit="3"),
        )
    )

    assert [candidate.opportunity_id for candidate in result.selected] == ["b", "c"]
    assert result.allocated_capital_usdt == Decimal("140")
    assert result.remaining_capital_usdt == Decimal("10")
    rejected = next(
        decision for decision in result.decisions if decision.candidate.opportunity_id == "a"
    )
    assert rejected.reason is AllocationReason.INSUFFICIENT_CAPITAL


def test_allocator_rejects_resource_conflicts_and_strategy_limits() -> None:
    allocator = CapitalAwareAllocator(
        AllocationConfig(
            total_capital_usdt=Decimal("200"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("100"),
            strategy_limits=(
                StrategyCapitalLimit("funding", 1, Decimal("70")),
            ),
        )
    )
    result = allocator.allocate(
        (
            _candidate(
                "triangle-btc",
                edge="15",
                resources=("binance:BTCUSDT",),
            ),
            _candidate(
                "funding-btc",
                strategy="funding",
                capital="60",
                edge="14",
                resources=("binance:BTCUSDT", "binance:BTCUSDT-PERP"),
            ),
            _candidate(
                "funding-eth",
                strategy="funding",
                capital="60",
                edge="13",
                resources=("binance:ETHUSDT", "binance:ETHUSDT-PERP"),
            ),
            _candidate(
                "funding-sol",
                strategy="funding",
                capital="20",
                edge="12",
                resources=("binance:SOLUSDT", "binance:SOLUSDT-PERP"),
            ),
        )
    )

    reasons = {
        decision.candidate.opportunity_id: decision.reason for decision in result.decisions
    }
    assert reasons["triangle-btc"] is AllocationReason.SELECTED
    assert reasons["funding-btc"] is AllocationReason.RESOURCE_CONFLICT
    assert reasons["funding-eth"] is AllocationReason.SELECTED
    assert reasons["funding-sol"] is AllocationReason.STRATEGY_POSITION_LIMIT


def test_allocator_accounts_for_persistent_capital_positions_and_resources() -> None:
    allocator = CapitalAwareAllocator(
        AllocationConfig(
            total_capital_usdt=Decimal("250"),
            cash_reserve_usdt=Decimal("50"),
            max_positions=2,
            max_capital_per_opportunity_usdt=Decimal("100"),
        )
    )
    result = allocator.allocate(
        (
            _candidate(
                "btc-conflict",
                capital="40",
                edge="20",
                resources=("spot:binance:BTC/USDT",),
            ),
            _candidate(
                "eth",
                capital="70",
                edge="10",
                resources=("spot:binance:ETH/USDT",),
            ),
            _candidate(
                "sol",
                capital="30",
                edge="9",
                resources=("spot:binance:SOL/USDT",),
            ),
        ),
        reserved_capital_usdt=Decimal("120"),
        open_positions=1,
        used_resources=frozenset({"spot:binance:BTC/USDT"}),
        strategy_capital_usdt={"funding": Decimal("120")},
        strategy_positions={"funding": 1},
    )

    reasons = {
        decision.candidate.opportunity_id: decision.reason for decision in result.decisions
    }
    assert reasons["btc-conflict"] is AllocationReason.RESOURCE_CONFLICT
    assert reasons["eth"] is AllocationReason.SELECTED
    assert reasons["sol"] is AllocationReason.PORTFOLIO_POSITION_LIMIT
    assert result.allocated_capital_usdt == Decimal("190")
    assert result.remaining_capital_usdt == Decimal("10")


def test_allocator_persistent_state_counts_against_strategy_limits() -> None:
    allocator = CapitalAwareAllocator(
        AllocationConfig(
            total_capital_usdt=Decimal("300"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("100"),
            strategy_limits=(
                StrategyCapitalLimit("funding", 1, Decimal("150")),
            ),
        )
    )
    result = allocator.allocate(
        (
            _candidate(
                "another-funding",
                strategy="funding",
                capital="50",
                resources=("spot:binance:ETH/USDT",),
            ),
        ),
        reserved_capital_usdt=Decimal("100"),
        open_positions=1,
        strategy_capital_usdt={"funding": Decimal("100")},
        strategy_positions={"funding": 1},
    )

    assert result.selected == ()
    assert result.decisions[0].reason is AllocationReason.STRATEGY_POSITION_LIMIT


def test_allocator_rejects_bad_candidates_and_duplicate_ids() -> None:
    allocator = CapitalAwareAllocator(
        AllocationConfig(
            total_capital_usdt=Decimal("100"),
            max_capital_per_opportunity_usdt=Decimal("50"),
            min_expected_edge_bps=Decimal("3"),
        )
    )
    result = allocator.allocate(
        (
            _candidate("loss", profit="-1", edge="20"),
            _candidate("small-edge", profit="1", edge="2"),
            _candidate("oversized", capital="60", profit="2", edge="10"),
        )
    )
    reasons = {decision.candidate.opportunity_id: decision.reason for decision in result.decisions}
    assert reasons == {
        "loss": AllocationReason.NON_POSITIVE_PROFIT,
        "small-edge": AllocationReason.EDGE_TOO_SMALL,
        "oversized": AllocationReason.OPPORTUNITY_CAP_EXCEEDED,
    }

    duplicate = _candidate("same")
    with pytest.raises(ValueError, match="opportunity_id"):
        allocator.allocate((duplicate, duplicate))


def test_allocator_rejects_invalid_persistent_state() -> None:
    allocator = CapitalAwareAllocator(AllocationConfig(total_capital_usdt=Decimal("100")))
    with pytest.raises(ValueError, match="reserved capital"):
        allocator.allocate((), reserved_capital_usdt=Decimal("101"))
    with pytest.raises(ValueError, match="open_positions"):
        allocator.allocate((), open_positions=5)
    with pytest.raises(ValueError, match="strategy capital"):
        allocator.allocate((), strategy_capital_usdt={"funding": Decimal("-1")})
