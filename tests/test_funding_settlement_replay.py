from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.funding import FundingCarryConfig, FundingSnapshot
from novaarb.funding_settlement_replay import (
    FundingSettlementReplayConfig,
    replay_funding_settlements,
)
from novaarb.research import ResearchRecorder


def _book(*, market: MarketType, bid: str, ask: str, received_ms: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=market,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _config() -> FundingCarryConfig:
    return FundingCarryConfig(
        target_notional_usdt=Decimal("100"),
        funding_intervals=1,
        funding_haircut=Decimal("1"),
        spot_taker_fee_bps=Decimal("1"),
        futures_taker_fee_bps=Decimal("1"),
        exit_market_reserve_bps=Decimal("0"),
        basis_risk_reserve_bps=Decimal("0"),
        min_net_edge_bps=Decimal("1"),
        max_book_age_ms=1_000,
        max_book_skew_ms=500,
        max_funding_age_ms=10_000,
    )


def _write_entry(recorder: ResearchRecorder) -> None:
    config = _config()
    recorder.append_metadata(
        "funding_session",
        {
            "symbols": ("BTCUSDT",),
            "config": config,
            "funding_refresh_seconds": 60,
        },
    )
    recorder.append_funding(
        FundingSnapshot(
            symbol="BTCUSDT",
            funding_rate=Decimal("0.005"),
            next_funding_time_ms=5_000,
            mark_price=Decimal("100"),
            index_price=Decimal("100"),
            received_time_ms=1_000,
        )
    )
    recorder.append_book(
        _book(
            market=MarketType.SPOT,
            bid="99.9",
            ask="100.0",
            received_ms=1_001,
        )
    )
    recorder.append_book(
        _book(
            market=MarketType.PERPETUAL,
            bid="100.5",
            ask="100.6",
            received_ms=1_002,
        )
    )
    recorder.append_funding(
        FundingSnapshot(
            symbol="BTCUSDT",
            funding_rate=Decimal("0.005"),
            next_funding_time_ms=5_000,
            mark_price=Decimal("101"),
            index_price=Decimal("101"),
            received_time_ms=4_900,
        )
    )


def test_funding_settlement_replay_realizes_scheduled_payment_and_exit(tmp_path) -> None:
    path = tmp_path / "funding.jsonl"
    recorder = ResearchRecorder(path)
    _write_entry(recorder)
    recorder.append_book(
        _book(
            market=MarketType.SPOT,
            bid="101.0",
            ask="101.1",
            received_ms=5_010,
        )
    )
    recorder.append_book(
        _book(
            market=MarketType.PERPETUAL,
            bid="101.2",
            ask="101.3",
            received_ms=5_011,
        )
    )

    summary = replay_funding_settlements(
        str(path),
        replay_config=FundingSettlementReplayConfig(
            max_exit_wait_ms=100,
            max_exit_book_skew_ms=20,
            entry_cooldown_ms=5_000,
        ),
    )

    assert summary.completed_trades == 1
    assert summary.profitable_trades == 1
    assert summary.total_net_profit_quote > 0
    trade = summary.trades[0]
    assert trade.settlement_count == 1
    assert trade.settlements[0].scheduled_time_ms == 5_000
    assert trade.settlements[0].observed_rate_bps == Decimal("50.000")
    assert trade.funding_quote > 0
    assert trade.exit_time_ms == 5_011
    assert trade.net_profit_quote > 0


def test_funding_settlement_replay_reports_missing_exit_books(tmp_path) -> None:
    path = tmp_path / "funding.jsonl"
    recorder = ResearchRecorder(path)
    _write_entry(recorder)

    summary = replay_funding_settlements(
        str(path),
        replay_config=FundingSettlementReplayConfig(
            max_exit_wait_ms=100,
            max_exit_book_skew_ms=20,
            entry_cooldown_ms=5_000,
        ),
    )

    assert summary.completed_trades == 0
    assert summary.incomplete_reasons == {"missing_exit_book": 1}
