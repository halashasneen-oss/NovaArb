from __future__ import annotations

from decimal import Decimal

from novaarb.allocator import AllocationConfig, AllocationReason
from novaarb.candidate_bus import (
    CROSS_VENUE_STRATEGY,
    FUNDING_STRATEGY,
    ShadowCandidateBus,
    cross_venue_envelope,
    funding_envelope,
)
from novaarb.cross_venue import CrossVenueOpportunity
from novaarb.domain import MarketType
from novaarb.funding import FundingCarryOpportunity
from novaarb.venue import Instrument


def _cross() -> CrossVenueOpportunity:
    return CrossVenueOpportunity(
        instrument=Instrument("BTC", "USDT", MarketType.SPOT),
        buy_venue="binance",
        sell_venue="bybit",
        base_quantity=Decimal("1"),
        buy_average_price=Decimal("100"),
        sell_average_price=Decimal("102"),
        buy_quote_required=Decimal("100"),
        sell_quote_proceeds=Decimal("102"),
        gross_spread_quote=Decimal("2"),
        fee_cost_quote=Decimal("0.2"),
        execution_reserve_quote=Decimal("0.05"),
        rebalance_reserve_quote=Decimal("0.05"),
        net_profit_quote=Decimal("1.7"),
        net_edge_bps=Decimal("20"),
        created_time_ms=1_000,
        buy_book_age_ms=5,
        sell_book_age_ms=5,
        book_skew_ms=0,
    )


def _funding(symbol: str, *, edge_bps: str = "10") -> FundingCarryOpportunity:
    return FundingCarryOpportunity(
        symbol=symbol,
        strategy=FUNDING_STRATEGY,
        base_quantity=Decimal("1"),
        spot_entry_price=Decimal("100"),
        futures_entry_price=Decimal("101"),
        reference_notional_usdt=Decimal("100"),
        funding_rate_bps=Decimal("25"),
        current_basis_bps=Decimal("10"),
        expected_funding_usdt=Decimal("1"),
        entry_execution_cost_usdt=Decimal("0.05"),
        entry_fees_usdt=Decimal("0.05"),
        exit_reserve_usdt=Decimal("0.05"),
        basis_risk_reserve_usdt=Decimal("0.05"),
        expected_net_profit_usdt=Decimal("0.8"),
        expected_net_edge_bps=Decimal(edge_bps),
        created_time_ms=1_001,
        spot_book_age_ms=5,
        futures_book_age_ms=5,
        funding_age_ms=10,
    )


def _bus() -> ShadowCandidateBus:
    return ShadowCandidateBus(
        AllocationConfig(
            total_capital_usdt=Decimal("1000"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("1"),
        )
    )


def test_candidate_bus_prevents_cross_strategy_market_double_use() -> None:
    cross = cross_venue_envelope(_cross(), sequence=1)
    funding = funding_envelope(
        _funding("BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=2,
    )

    result = _bus().allocate((funding, cross))

    assert len(result.selected) == 1
    assert result.selected[0].family == CROSS_VENUE_STRATEGY
    decisions = {item.envelope.family: item for item in result.decisions}
    assert decisions[CROSS_VENUE_STRATEGY].selected is True
    assert decisions[FUNDING_STRATEGY].selected is False
    assert decisions[FUNDING_STRATEGY].reason is AllocationReason.RESOURCE_CONFLICT


def test_candidate_bus_can_allocate_unrelated_strategy_resources_together() -> None:
    cross = cross_venue_envelope(_cross(), sequence=1)
    funding = funding_envelope(
        _funding("ETHUSDT", edge_bps="8"),
        base_asset="ETH",
        quote_asset="USDT",
        sequence=2,
    )

    result = _bus().allocate((cross, funding))

    assert len(result.selected) == 2
    assert {item.family for item in result.selected} == {
        CROSS_VENUE_STRATEGY,
        FUNDING_STRATEGY,
    }
    assert result.allocated_capital_usdt == Decimal("402")
    assert result.remaining_capital_usdt == Decimal("598")


def test_funding_candidate_uses_conservative_unlevered_capital_multiplier() -> None:
    funding = funding_envelope(
        _funding("BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
        capital_multiplier=Decimal("2"),
    )

    assert funding.candidate.capital_required_usdt == Decimal("200")
    assert funding.candidate.resource_keys == (
        "spot:binance:BTC/USDT",
        "perpetual:binance:BTC/USDT",
    )
