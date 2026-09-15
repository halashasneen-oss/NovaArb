from decimal import Decimal

from novaarb.costs import ExecutableEdgeModel, FeeSchedule
from novaarb.domain import MarketType
from novaarb.risk import RiskEngine, RiskLimits, RiskReason
from novaarb.strategies.spot_perp import SpotPerpConfig, SpotPerpStrategy
from conftest import make_book


def _strategy() -> SpotPerpStrategy:
    fees = FeeSchedule(spot_taker_bps=Decimal("1"), futures_taker_bps=Decimal("1"))
    model = ExecutableEdgeModel(fees, latency_reserve_bps=Decimal("0"))
    return SpotPerpStrategy(
        config=SpotPerpConfig(target_notional_usd=Decimal("100")),
        fees=fees,
        edge_model=model,
    )


def test_profitable_cash_and_carry_passes_risk() -> None:
    spot = make_book(market=MarketType.SPOT, bid="99.9", ask="100.0")
    perp = make_book(market=MarketType.PERPETUAL, bid="101.0", ask="101.1")
    opportunity = _strategy().evaluate(spot, perp, now_ms=1_050)[0]

    assert opportunity.buy_market is MarketType.SPOT
    assert opportunity.sell_market is MarketType.PERPETUAL
    assert opportunity.costs.net_profit_usd > 0
    assert opportunity.costs.net_edge_bps > Decimal("50")

    decision = RiskEngine(
        RiskLimits(
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=100,
            max_notional_usd=Decimal("110"),
        )
    ).assess(opportunity)
    assert decision.approved is True
    assert decision.reason is RiskReason.APPROVED


def test_small_dislocation_fails_after_costs() -> None:
    spot = make_book(market=MarketType.SPOT, bid="99.95", ask="100.00")
    perp = make_book(market=MarketType.PERPETUAL, bid="100.01", ask="100.06")
    opportunity = _strategy().evaluate(spot, perp, now_ms=1_050)[0]
    decision = RiskEngine(
        RiskLimits(
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=100,
            max_notional_usd=Decimal("110"),
        )
    ).assess(opportunity)
    assert decision.approved is False
    assert decision.reason in {RiskReason.NON_POSITIVE_PROFIT, RiskReason.EDGE_TOO_SMALL}


def test_stale_books_are_rejected_even_when_edge_is_large() -> None:
    spot = make_book(market=MarketType.SPOT, bid="99.9", ask="100.0", received_ms=1_000)
    perp = make_book(market=MarketType.PERPETUAL, bid="102.0", ask="102.1", received_ms=1_000)
    opportunity = _strategy().evaluate(spot, perp, now_ms=2_000)[0]
    decision = RiskEngine(
        RiskLimits(
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=500,
            max_notional_usd=Decimal("110"),
        )
    ).assess(opportunity)
    assert decision.reason is RiskReason.STALE_BOOK
