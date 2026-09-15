from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from novaarb.domain import MarketType, ZERO
from novaarb.research import iter_records, snapshot_from_record


class VenueHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class VenueHealthConfig:
    stale_gap_ms: int = 2_000
    max_p95_feed_delay_ms: int = 1_000
    min_events: int = 2
    degraded_stale_ratio: Decimal = Decimal("0.01")
    unhealthy_stale_ratio: Decimal = Decimal("0.05")
    degraded_out_of_order_ratio: Decimal = Decimal("0.001")
    unhealthy_out_of_order_ratio: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.stale_gap_ms <= 0:
            raise ValueError("stale_gap_ms must be positive")
        if self.max_p95_feed_delay_ms < 0:
            raise ValueError("max_p95_feed_delay_ms cannot be negative")
        if self.min_events <= 0:
            raise ValueError("min_events must be positive")
        ratios = (
            self.degraded_stale_ratio,
            self.unhealthy_stale_ratio,
            self.degraded_out_of_order_ratio,
            self.unhealthy_out_of_order_ratio,
        )
        if any(value < ZERO or value > Decimal("1") for value in ratios):
            raise ValueError("health ratios must be between 0 and 1")
        if self.degraded_stale_ratio > self.unhealthy_stale_ratio:
            raise ValueError("degraded_stale_ratio cannot exceed unhealthy_stale_ratio")
        if self.degraded_out_of_order_ratio > self.unhealthy_out_of_order_ratio:
            raise ValueError(
                "degraded_out_of_order_ratio cannot exceed unhealthy_out_of_order_ratio"
            )


@dataclass(frozen=True, slots=True)
class VenueSymbolHealth:
    venue: str
    symbol: str
    market: MarketType
    state: VenueHealthState
    event_count: int
    duration_ms: int
    event_rate_hz: Decimal
    feed_delay_p50_ms: int
    feed_delay_p95_ms: int
    feed_delay_p99_ms: int
    feed_delay_max_ms: int
    interarrival_p50_ms: int
    interarrival_p95_ms: int
    interarrival_p99_ms: int
    interarrival_max_ms: int
    stale_gap_count: int
    stale_gap_ratio: Decimal
    out_of_order_count: int
    out_of_order_ratio: Decimal


@dataclass(frozen=True, slots=True)
class VenueHealthReport:
    source: str
    total_book_events: int
    invalid_book_records: int
    healthy_streams: int
    degraded_streams: int
    unhealthy_streams: int
    streams: tuple[VenueSymbolHealth, ...]


@dataclass(slots=True)
class _StreamAccumulator:
    venue: str
    symbol: str
    market: MarketType
    received_times: list[int]
    feed_delays: list[int]
    out_of_order_count: int = 0

    def add(self, *, received_ms: int, event_ms: int) -> None:
        if self.received_times and received_ms < self.received_times[-1]:
            self.out_of_order_count += 1
        self.received_times.append(received_ms)
        self.feed_delays.append(max(0, received_ms - event_ms))


def _percentile(values: list[int], quantile: Decimal) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(float(quantile) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return ZERO
    return Decimal(numerator) / Decimal(denominator)


def _classify(
    *,
    event_count: int,
    p95_delay_ms: int,
    stale_ratio: Decimal,
    out_of_order_ratio: Decimal,
    config: VenueHealthConfig,
) -> VenueHealthState:
    if event_count < config.min_events:
        return VenueHealthState.UNHEALTHY
    if (
        stale_ratio > config.unhealthy_stale_ratio
        or out_of_order_ratio > config.unhealthy_out_of_order_ratio
        or p95_delay_ms > config.max_p95_feed_delay_ms * 2
    ):
        return VenueHealthState.UNHEALTHY
    if (
        stale_ratio > config.degraded_stale_ratio
        or out_of_order_ratio > config.degraded_out_of_order_ratio
        or p95_delay_ms > config.max_p95_feed_delay_ms
    ):
        return VenueHealthState.DEGRADED
    return VenueHealthState.HEALTHY


def analyze_venue_health(
    path: str | Path,
    *,
    config: VenueHealthConfig | None = None,
) -> VenueHealthReport:
    """Build venue/symbol feed-health evidence from a recorded research capture."""

    resolved = config or VenueHealthConfig()
    streams: dict[tuple[str, str, MarketType], _StreamAccumulator] = {}
    invalid = 0
    total = 0

    for record in iter_records(path):
        if record.get("kind") != "book":
            continue
        try:
            snapshot = snapshot_from_record(record)
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        total += 1
        key = (snapshot.venue, snapshot.symbol, snapshot.market)
        accumulator = streams.get(key)
        if accumulator is None:
            accumulator = _StreamAccumulator(
                venue=snapshot.venue,
                symbol=snapshot.symbol,
                market=snapshot.market,
                received_times=[],
                feed_delays=[],
            )
            streams[key] = accumulator
        accumulator.add(
            received_ms=snapshot.received_time_ms,
            event_ms=snapshot.event_time_ms,
        )

    results: list[VenueSymbolHealth] = []
    for key in sorted(streams, key=lambda item: (item[0], item[1], item[2].value)):
        accumulator = streams[key]
        times = accumulator.received_times
        interarrival = [
            current - previous
            for previous, current in zip(times, times[1:], strict=False)
            if current >= previous
        ]
        stale_count = sum(1 for gap in interarrival if gap > resolved.stale_gap_ms)
        gap_count = len(interarrival)
        stale_ratio = _ratio(stale_count, gap_count)
        out_ratio = _ratio(accumulator.out_of_order_count, max(1, len(times) - 1))
        first_ms = min(times) if times else 0
        last_ms = max(times) if times else 0
        duration_ms = max(0, last_ms - first_ms)
        event_rate = (
            Decimal(len(times)) * Decimal("1000") / Decimal(duration_ms)
            if duration_ms > 0
            else ZERO
        )
        p95_delay = _percentile(accumulator.feed_delays, Decimal("0.95"))
        state = _classify(
            event_count=len(times),
            p95_delay_ms=p95_delay,
            stale_ratio=stale_ratio,
            out_of_order_ratio=out_ratio,
            config=resolved,
        )
        results.append(
            VenueSymbolHealth(
                venue=accumulator.venue,
                symbol=accumulator.symbol,
                market=accumulator.market,
                state=state,
                event_count=len(times),
                duration_ms=duration_ms,
                event_rate_hz=event_rate,
                feed_delay_p50_ms=_percentile(
                    accumulator.feed_delays, Decimal("0.50")
                ),
                feed_delay_p95_ms=p95_delay,
                feed_delay_p99_ms=_percentile(
                    accumulator.feed_delays, Decimal("0.99")
                ),
                feed_delay_max_ms=max(accumulator.feed_delays, default=0),
                interarrival_p50_ms=_percentile(interarrival, Decimal("0.50")),
                interarrival_p95_ms=_percentile(interarrival, Decimal("0.95")),
                interarrival_p99_ms=_percentile(interarrival, Decimal("0.99")),
                interarrival_max_ms=max(interarrival, default=0),
                stale_gap_count=stale_count,
                stale_gap_ratio=stale_ratio,
                out_of_order_count=accumulator.out_of_order_count,
                out_of_order_ratio=out_ratio,
            )
        )

    healthy = sum(1 for item in results if item.state is VenueHealthState.HEALTHY)
    degraded = sum(1 for item in results if item.state is VenueHealthState.DEGRADED)
    unhealthy = sum(1 for item in results if item.state is VenueHealthState.UNHEALTHY)
    return VenueHealthReport(
        source=str(path),
        total_book_events=total,
        invalid_book_records=invalid,
        healthy_streams=healthy,
        degraded_streams=degraded,
        unhealthy_streams=unhealthy,
        streams=tuple(results),
    )
