from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.funding import FundingCarryConfig, FundingSnapshot
from novaarb.funding_replay import funding_from_record, replay_funding_log
from novaarb.research import ResearchRecorder, iter_records


def _book(market: MarketType, bid: str, ask: str, timestamp: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=market,
        bids=(BookLevel(Decimal(bid), Decimal("100")),),
        asks=(BookLevel(Decimal(ask), Decimal("100")),),
        event_time_ms=timestamp,
        received_time_ms=timestamp,
    )


def test_funding_replay_restores_session_and_projected_opportunities(tmp_path) -> None:
    path = tmp_path / "funding.jsonl"
    recorder = ResearchRecorder(path)
    config = FundingCarryConfig(
        target_notional_usdt=Decimal("100"),
        funding_intervals=1,
        funding_haircut=Decimal("1"),
        spot_taker_fee_bps=Decimal("0"),
        futures_taker_fee_bps=Decimal("0"),
        exit_market_reserve_bps=Decimal("0"),
        basis_risk_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("1"),
        max_book_age_ms=500,
        max_book_skew_ms=100,
        max_funding_age_ms=10_000,
    )
    recorder.append_metadata(
        "funding_session",
        {"symbols": ("BTCUSDT",), "config": config, "funding_refresh_seconds": 60},
    )
    funding = FundingSnapshot(
        symbol="BTCUSDT",
        funding_rate=Decimal("0.001"),
        next_funding_time_ms=10_000,
        mark_price=Decimal("100"),
        index_price=Decimal("100"),
        received_time_ms=1_000,
    )
    recorder.append_funding(funding)
    for timestamp in (1_000, 1_100, 1_200):
        recorder.append_book(_book(MarketType.SPOT, "99.99", "100.00", timestamp))
        recorder.append_book(_book(MarketType.PERPETUAL, "100.00", "100.01", timestamp))

    records = list(iter_records(path))
    funding_record = next(record for record in records if record["kind"] == "funding")
    restored = funding_from_record(funding_record)
    assert restored == funding

    summary = replay_funding_log(str(path), gap_tolerance_ms=500)
    assert summary.book_snapshots == 6
    assert summary.funding_updates == 1
    assert summary.evaluations > 0
    assert summary.approved_observations > 0
    assert summary.opportunity_windows >= 1
    assert summary.max_projected_edge_bps > Decimal("1")
    assert summary.symbols[0].symbol == "BTCUSDT"
    assert summary.symbols[0].max_funding_rate_bps == Decimal("10.000")
