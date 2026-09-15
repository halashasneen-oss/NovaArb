from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.funding import (
    FundingCarryConfig,
    FundingCarryReason,
    FundingCarryScanner,
    FundingCarryStrategy,
    FundingSnapshot,
    parse_funding_payload,
)


def _book(market: MarketType, bid: str, ask: str, timestamp: int = 1_000) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=market,
        bids=(BookLevel(Decimal(bid), Decimal("100")),),
        asks=(BookLevel(Decimal(ask), Decimal("100")),),
        event_time_ms=timestamp,
        received_time_ms=timestamp,
    )


def _funding(rate: str = "0.001", timestamp: int = 1_000) -> FundingSnapshot:
    return FundingSnapshot(
        symbol="BTCUSDT",
        funding_rate=Decimal(rate),
        next_funding_time_ms=2_000,
        mark_price=Decimal("100"),
        index_price=Decimal("100"),
        received_time_ms=timestamp,
    )


def test_parse_funding_payload() -> None:
    snapshots = parse_funding_payload(
        {
            "symbol": "BTCUSDT",
            "lastFundingRate": "0.0001",
            "nextFundingTime": 123456,
            "markPrice": "100.5",
            "indexPrice": "100.4",
        },
        received_time_ms=1_000,
    )
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.funding_rate_bps == Decimal("1.0000")
    assert snapshot.received_time_ms == 1_000


def test_positive_funding_can_survive_execution_reserves() -> None:
    config = FundingCarryConfig(
        target_notional_usdt=Decimal("100"),
        funding_intervals=1,
        funding_haircut=Decimal("1"),
        spot_taker_fee_bps=Decimal("0"),
        futures_taker_fee_bps=Decimal("0"),
        exit_market_reserve_bps=Decimal("0"),
        basis_risk_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("2"),
    )
    strategy = FundingCarryStrategy(config)
    opportunity = strategy.evaluate(
        _book(MarketType.SPOT, "99.99", "100.00"),
        _book(MarketType.PERPETUAL, "100.00", "100.01"),
        _funding("0.001"),
        now_ms=1_000,
    )
    assert opportunity is not None
    assert opportunity.expected_funding_usdt > 0
    assert opportunity.expected_net_profit_usdt > 0
    assert opportunity.expected_net_edge_bps > Decimal("2")
    decision = strategy.assess(opportunity)
    assert decision.approved
    assert decision.reason is FundingCarryReason.APPROVED


def test_scanner_requires_both_books_and_funding() -> None:
    config = FundingCarryConfig(
        target_notional_usdt=Decimal("100"),
        funding_haircut=Decimal("1"),
        spot_taker_fee_bps=Decimal("0"),
        futures_taker_fee_bps=Decimal("0"),
        exit_market_reserve_bps=Decimal("0"),
        basis_risk_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("1"),
    )
    scanner = FundingCarryScanner(symbols=("BTCUSDT",), config=config)
    scanner.update_funding((_funding(),))
    assert scanner.process_snapshot(_book(MarketType.SPOT, "99.99", "100.00")) is None
    event = scanner.process_snapshot(_book(MarketType.PERPETUAL, "100.00", "100.01"))
    assert event is not None
    assert event.opportunity.symbol == "BTCUSDT"
    assert event.decision.approved
