from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder
from novaarb.venue_health import (
    VenueHealthConfig,
    VenueHealthState,
    analyze_venue_health,
)


def _book(*, received_ms: int, delay_ms: int = 10) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal("99"), Decimal("2")),),
        asks=(BookLevel(Decimal("100"), Decimal("2")),),
        event_time_ms=received_ms - delay_ms,
        received_time_ms=received_ms,
    )


def test_venue_health_reports_healthy_low_latency_stream(tmp_path) -> None:
    path = tmp_path / "capture.jsonl"
    recorder = ResearchRecorder(path)
    for timestamp in (1_000, 1_100, 1_200, 1_300):
        recorder.append_book(_book(received_ms=timestamp))

    report = analyze_venue_health(
        path,
        config=VenueHealthConfig(
            stale_gap_ms=500,
            max_p95_feed_delay_ms=50,
        ),
    )

    assert report.total_book_events == 4
    assert report.healthy_streams == 1
    assert report.degraded_streams == 0
    assert report.unhealthy_streams == 0
    stream = report.streams[0]
    assert stream.state is VenueHealthState.HEALTHY
    assert stream.feed_delay_p95_ms == 10
    assert stream.interarrival_p95_ms == 100
    assert stream.stale_gap_count == 0
    assert stream.out_of_order_count == 0
    assert stream.event_rate_hz > 0


def test_venue_health_flags_stale_and_out_of_order_capture(tmp_path) -> None:
    path = tmp_path / "capture.jsonl"
    recorder = ResearchRecorder(path)
    for timestamp in (1_000, 1_100, 5_000, 4_900):
        recorder.append_book(_book(received_ms=timestamp, delay_ms=200))

    report = analyze_venue_health(
        path,
        config=VenueHealthConfig(
            stale_gap_ms=1_000,
            max_p95_feed_delay_ms=100,
            unhealthy_stale_ratio=Decimal("0.20"),
            unhealthy_out_of_order_ratio=Decimal("0.20"),
        ),
    )

    stream = report.streams[0]
    assert stream.state is VenueHealthState.UNHEALTHY
    assert stream.stale_gap_count == 1
    assert stream.out_of_order_count == 1
    assert stream.feed_delay_p95_ms == 200
