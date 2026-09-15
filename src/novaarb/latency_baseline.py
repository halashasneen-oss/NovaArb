from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from novaarb.domain import MarketType
from novaarb.research import iter_records, snapshot_from_record


@dataclass(frozen=True, slots=True)
class LatencyStreamBaseline:
    venue: str
    symbol: str
    market: MarketType
    capture_count: int
    event_count: int
    first_received_ms: int
    last_received_ms: int
    observation_span_ms: int
    feed_delay_p50_ms: int
    feed_delay_p95_ms: int
    feed_delay_p99_ms: int
    feed_delay_max_ms: int


@dataclass(frozen=True, slots=True)
class LatencyBaselineReport:
    sources: tuple[str, ...]
    source_count: int
    total_book_events: int
    invalid_book_records: int
    first_received_ms: int
    last_received_ms: int
    observation_span_ms: int
    streams: tuple[LatencyStreamBaseline, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "sources": list(self.sources),
            "source_count": self.source_count,
            "total_book_events": self.total_book_events,
            "invalid_book_records": self.invalid_book_records,
            "first_received_ms": self.first_received_ms,
            "last_received_ms": self.last_received_ms,
            "observation_span_ms": self.observation_span_ms,
            "streams": [
                {
                    **asdict(stream),
                    "market": stream.market.value,
                }
                for stream in self.streams
            ],
        }


@dataclass(slots=True)
class _Accumulator:
    venue: str
    symbol: str
    market: MarketType
    delays: list[int]
    received: list[int]
    sources: set[str]


def _percentile(values: list[int], quantile: Decimal) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(float(quantile) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def build_latency_baseline(paths: tuple[str | Path, ...]) -> LatencyBaselineReport:
    """Aggregate event-to-receive delay across one or more public capture files.

    The report measures the latency observed by the machine that created the captures. It does not
    infer exchange matching-engine latency and it is only a deployment-host baseline when the
    captures were produced on the intended deployment host.
    """

    if not paths:
        raise ValueError("at least one capture path is required")
    sources = tuple(str(Path(path)) for path in paths)
    if len(set(sources)) != len(sources):
        raise ValueError("capture paths must be unique")

    accumulators: dict[tuple[str, str, MarketType], _Accumulator] = {}
    invalid = 0
    total = 0
    all_received: list[int] = []

    for source in sources:
        for record in iter_records(source):
            if record.get("kind") != "book":
                continue
            try:
                snapshot = snapshot_from_record(record)
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            total += 1
            all_received.append(snapshot.received_time_ms)
            key = (snapshot.venue, snapshot.symbol, snapshot.market)
            accumulator = accumulators.get(key)
            if accumulator is None:
                accumulator = _Accumulator(
                    venue=snapshot.venue,
                    symbol=snapshot.symbol,
                    market=snapshot.market,
                    delays=[],
                    received=[],
                    sources=set(),
                )
                accumulators[key] = accumulator
            accumulator.delays.append(
                max(0, snapshot.received_time_ms - snapshot.event_time_ms)
            )
            accumulator.received.append(snapshot.received_time_ms)
            accumulator.sources.add(source)

    streams: list[LatencyStreamBaseline] = []
    for key in sorted(accumulators, key=lambda item: (item[0], item[1], item[2].value)):
        item = accumulators[key]
        first = min(item.received)
        last = max(item.received)
        streams.append(
            LatencyStreamBaseline(
                venue=item.venue,
                symbol=item.symbol,
                market=item.market,
                capture_count=len(item.sources),
                event_count=len(item.received),
                first_received_ms=first,
                last_received_ms=last,
                observation_span_ms=max(0, last - first),
                feed_delay_p50_ms=_percentile(item.delays, Decimal("0.50")),
                feed_delay_p95_ms=_percentile(item.delays, Decimal("0.95")),
                feed_delay_p99_ms=_percentile(item.delays, Decimal("0.99")),
                feed_delay_max_ms=max(item.delays, default=0),
            )
        )

    first_received = min(all_received, default=0)
    last_received = max(all_received, default=0)
    return LatencyBaselineReport(
        sources=sources,
        source_count=len(sources),
        total_book_events=total,
        invalid_book_records=invalid,
        first_received_ms=first_received,
        last_received_ms=last_received,
        observation_span_ms=max(0, last_received - first_received),
        streams=tuple(streams),
    )
