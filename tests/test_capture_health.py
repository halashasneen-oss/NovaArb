from __future__ import annotations

from decimal import Decimal

from novaarb.capture_health import summarize_capture
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder


def _book(symbol: str, event_ms: int, received_ms: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal("99"), Decimal("10")),),
        asks=(BookLevel(Decimal("100"), Decimal("10")),),
        event_time_ms=event_ms,
        received_time_ms=received_ms,
    )


def test_capture_health_tracks_delay_rate_and_gaps(tmp_path) -> None:
    path = tmp_path / "capture.jsonl"
    recorder = ResearchRecorder(path)
    for book in (
        _book("BTCUSDT", 990, 1_000),
        _book("ETHUSDT", 995, 1_010),
        _book("BTCUSDT", 1_015, 1_025),
        _book("ETHUSDT", 1_020, 1_050),
        _book("BTCUSDT", 1_080, 1_100),
    ):
        recorder.append_book(book)

    summary = summarize_capture(str(path))

    assert summary.snapshots == 5
    assert summary.duration_ms == 100
    assert summary.events_per_second == 50.0
    assert summary.median_feed_delay_ms == 15
    assert summary.p95_feed_delay_ms == 30
    assert summary.p99_feed_delay_ms == 30
    assert summary.max_interarrival_gap_ms == 50
    assert summary.negative_clock_delay_events == 0
    assert [stats.symbol for stats in summary.symbols] == ["BTCUSDT", "ETHUSDT"]
    btc = summary.symbols[0]
    assert btc.snapshots == 3
    assert btc.max_interarrival_gap_ms == 75
