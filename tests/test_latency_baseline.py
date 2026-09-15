from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.latency_baseline import build_latency_baseline
from novaarb.research import ResearchRecorder


def _book(
    *,
    venue: str,
    symbol: str,
    event_ms: int,
    received_ms: int,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol=symbol,
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal("99"), Decimal("10")),),
        asks=(BookLevel(Decimal("100"), Decimal("10")),),
        event_time_ms=event_ms,
        received_time_ms=received_ms,
    )


def test_latency_baseline_aggregates_multiple_captures(tmp_path) -> None:
    first = tmp_path / "day1.jsonl"
    second = tmp_path / "day2.jsonl"
    recorder = ResearchRecorder(first)
    recorder.append_book(
        _book(venue="binance", symbol="BTCUSDT", event_ms=1_000, received_ms=1_010)
    )
    recorder.append_book(
        _book(venue="binance", symbol="BTCUSDT", event_ms=1_100, received_ms=1_120)
    )
    recorder = ResearchRecorder(second)
    recorder.append_book(
        _book(venue="binance", symbol="BTCUSDT", event_ms=2_000, received_ms=2_030)
    )
    recorder.append_book(
        _book(venue="bybit", symbol="BTCUSDT", event_ms=2_000, received_ms=2_040)
    )

    report = build_latency_baseline((first, second))

    assert report.source_count == 2
    assert report.total_book_events == 4
    assert report.first_received_ms == 1_010
    assert report.last_received_ms == 2_040
    assert report.observation_span_ms == 1_030
    assert len(report.streams) == 2
    binance = next(stream for stream in report.streams if stream.venue == "binance")
    assert binance.capture_count == 2
    assert binance.event_count == 3
    assert binance.feed_delay_p50_ms == 20
    assert binance.feed_delay_p95_ms == 30
    assert binance.feed_delay_p99_ms == 30
    assert binance.feed_delay_max_ms == 30
    payload = report.to_payload()
    assert payload["streams"][0]["market"] == "spot"


def test_latency_baseline_requires_unique_paths(tmp_path) -> None:
    path = tmp_path / "capture.jsonl"
    ResearchRecorder(path).append_book(
        _book(venue="binance", symbol="ETHUSDT", event_ms=1, received_ms=2)
    )

    try:
        build_latency_baseline((path, path))
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate capture paths should fail")
