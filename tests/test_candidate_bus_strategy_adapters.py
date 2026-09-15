from __future__ import annotations

from decimal import Decimal

from novaarb.allocator import AllocationConfig, AllocationReason
from novaarb.candidate_bus import (
    FUNDING_STRATEGY,
    SPOT_PERP_STRATEGY,
    SYNTHETIC_STRATEGY,
    TRIANGULAR_STRATEGY,
    ShadowCandidateBus,
    funding_envelope,
    spot_perp_envelope,
    synthetic_envelope,
    triangular_envelope,
)
from novaarb.domain import (
    ArbitrageOpportunity,
    BookFill,
    CostBreakdown,
    MarketType,
    Side,
)
from novaarb.funding import FundingCarryOpportunity
from novaarb.triangular import (
    ConversionFill,
    ConversionSide,
    TriangleRoute,
    TriangularOpportunity,
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


def _book_fill(side: Side, quote: str, average: str) -> BookFill:
    return BookFill(
        side=side,
        base_quantity=Decimal("1"),
        quote_quantity=Decimal(quote),
        average_price=Decimal(average),
        top_price=Decimal(average),
        depth_slippage_usd=Decimal("0"),
        levels_used=1,
    )


def _spot_perp() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        strategy=SPOT_PERP_STRATEGY,
        venue="binance",
        symbol="BTCUSDT",
        buy_market=MarketType.SPOT,
        sell_market=MarketType.PERPETUAL,
        base_quantity=Decimal("1"),
        buy_fill=_book_fill(Side.BUY, "100", "100"),
        sell_fill=_book_fill(Side.SELL, "101", "101"),
        costs=CostBreakdown(
            gross_dislocation_usd=Decimal("1"),
            entry_spread_cost_usd=Decimal("0"),
            depth_slippage_usd=Decimal("0"),
            entry_fees_usd=Decimal("0.2"),
            latency_reserve_usd=Decimal("0.1"),
            exit_reserve_usd=Decimal("0.1"),
            funding_reserve_usd=Decimal("0"),
            net_capture_usd=Decimal("0.6"),
            net_edge_bps=Decimal("15"),
        ),
        created_time_ms=1_000,
        buy_book_age_ms=5,
        sell_book_age_ms=5,
    )


def _funding() -> FundingCarryOpportunity:
    return FundingCarryOpportunity(
        symbol="BTCUSDT",
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
        expected_net_edge_bps=Decimal("10"),
        created_time_ms=1_001,
        spot_book_age_ms=5,
        futures_book_age_ms=5,
        funding_age_ms=10,
    )


def _leg(
    *,
    symbol: str,
    from_asset: str,
    to_asset: str,
    side: ConversionSide,
) -> ConversionFill:
    return ConversionFill(
        symbol=symbol,
        from_asset=from_asset,
        to_asset=to_asset,
        side=side,
        input_amount=Decimal("100"),
        consumed_input=Decimal("100"),
        gross_output=Decimal("101"),
        fee_paid=Decimal("0.1"),
        net_output=Decimal("100.9"),
        average_price=Decimal("1"),
        levels_used=1,
        residual_input=Decimal("0"),
    )


def _triangle() -> TriangularOpportunity:
    return TriangularOpportunity(
        strategy="triangular",
        route=TriangleRoute(
            anchor_asset="USDT",
            first_asset="BTC",
            second_asset="ETH",
            symbols=("BTCUSDT", "ETHBTC", "ETHUSDT"),
        ),
        starting_amount=Decimal("100"),
        final_amount_before_reserve=Decimal("100.20"),
        execution_reserve=Decimal("0.05"),
        net_final_amount=Decimal("100.15"),
        net_profit=Decimal("0.15"),
        net_edge_bps=Decimal("5"),
        legs=(
            _leg(
                symbol="BTCUSDT",
                from_asset="USDT",
                to_asset="BTC",
                side=ConversionSide.BUY_BASE,
            ),
            _leg(
                symbol="ETHBTC",
                from_asset="BTC",
                to_asset="ETH",
                side=ConversionSide.BUY_BASE,
            ),
            _leg(
                symbol="ETHUSDT",
                from_asset="ETH",
                to_asset="USDT",
                side=ConversionSide.SELL_BASE,
            ),
        ),
        created_time_ms=1_002,
        max_book_age_ms=5,
        book_skew_ms=2,
    )


def test_spot_perp_adapter_conflicts_with_funding_on_same_markets() -> None:
    spot_perp = spot_perp_envelope(
        _spot_perp(),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )
    funding = funding_envelope(
        _funding(),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=2,
    )

    result = _bus().allocate((funding, spot_perp))

    assert result.selected == (spot_perp,)
    decision = next(item for item in result.decisions if item.envelope is funding)
    assert decision.reason is AllocationReason.RESOURCE_CONFLICT
    assert spot_perp.candidate.resource_keys == (
        "spot:binance:BTC/USDT",
        "perpetual:binance:BTC/USDT",
    )


def test_triangle_adapter_uses_canonical_spot_market_resources() -> None:
    envelope = triangular_envelope(_triangle(), venue="binance", sequence=1)

    assert envelope.family == TRIANGULAR_STRATEGY
    assert envelope.candidate.resource_keys == (
        "spot:binance:BTC/USDT",
        "spot:binance:ETH/BTC",
        "spot:binance:ETH/USDT",
    )
    assert envelope.candidate.capital_required_usdt == Decimal("100")
    assert envelope.candidate.expected_net_profit_usdt == Decimal("0.15")


def test_synthetic_adapter_reuses_triangle_execution_economics_with_distinct_family() -> None:
    envelope = synthetic_envelope(_triangle(), venue="binance", sequence=3)

    assert envelope.family == SYNTHETIC_STRATEGY
    assert envelope.candidate.strategy == SYNTHETIC_STRATEGY
    assert envelope.candidate.expected_edge_bps == Decimal("5")
    assert envelope.candidate.resource_keys[0] == "spot:binance:BTC/USDT"
