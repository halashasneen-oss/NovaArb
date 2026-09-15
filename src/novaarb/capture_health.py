from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from statistics import median

from novaarb.research import iter_records, snapshot_from_record


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[max(0, min(len(ordered) - 1, index))]


@dataclass(frozen=True, slots=True)
class SymbolFeedStats:
    symbol: str
    snapshots: int
    duration_ms: int
    events_per_second: float
    median_feed_delay_ms: int
    p95_feed_delay_ms: int
    p99_feed_delay_ms: int
    max_interarrival_gap_ms: int


@dataclass(frozen=True, slots=True)
class CaptureHealthSummary:
    snapshots: int
    duration_ms: int
    events_per_second: float
    median_feed_delay_ms: int
    p95_feed_delay_ms: int
    p99_feed_delay_ms: int
    max_interarrival_gap_ms: int
    negative_clock_delay_events: int
    symbols: tuple[SymbolFeedStats, ...]


def _symbol_stats(symbol: str, snapshots: list[object]) -> SymbolFeedStats:
    ordered = sorted(snapshots, key=lambda item: item.received_time_ms)
    received = [item.received_time_ms for item in ordered]
    delays = [max(0, item.received_time_ms - item.event_time_ms) for item in ordered]
    gaps = [later - earlier for earlier, later in zip(received, received[1:], strict=False)]
    duration_ms = received[-1] - received[0] if len(received) > 1 else 0
    seconds = duration_ms / 1000
    rate = len(ordered) / seconds if seconds > 0 else float(len(ordered))
    return SymbolFeedStats(
        symbol=symbol,
        snapshots=len(ordered),
        duration_ms=duration_ms,
        events_per_second=rate,
        median_feed_delay_ms=int(median(delays)) if delays else 0,
        p95_feed_delay_ms=_percentile(delays, 0.95),
        p99_feed_delay_ms=_percentile(delays, 0.99),
        max_interarrival_gap_ms=max(gaps, default=0),
    )


def summarize_capture(path: str) -> CaptureHealthSummary:
    snapshots = [
        snapshot_from_record(record)
        for record in iter_records(path)
        if record.get("kind") == "book"
    ]
    if not snapshots:
        raise ValueError("research log contains no order-book snapshots")

    ordered = sorted(snapshots, key=lambda item: item.received_time_ms)
    received = [snapshot.received_time_ms for snapshot in ordered]
    raw_delays = [snapshot.received_time_ms - snapshot.event_time_ms for snapshot in ordered]
    delays = [max(0, value) for value in raw_delays]
    gaps = [later - earlier for earlier, later in zip(received, received[1:], strict=False)]
    duration_ms = received[-1] - received[0] if len(received) > 1 else 0
    seconds = duration_ms / 1000
    rate = len(ordered) / seconds if seconds > 0 else float(len(ordered))

    by_symbol: dict[str, list[object]] = {}
    for snapshot in ordered:
        by_symbol.setdefault(snapshot.symbol, []).append(snapshot)

    symbol_stats = tuple(
        sorted(
            (_symbol_stats(symbol, values) for symbol, values in by_symbol.items()),
            key=lambda item: item.symbol,
        )
    )
    return CaptureHealthSummary(
        snapshots=len(ordered),
        duration_ms=duration_ms,
        events_per_second=rate,
        median_feed_delay_ms=int(median(delays)) if delays else 0,
        p95_feed_delay_ms=_percentile(delays, 0.95),
        p99_feed_delay_ms=_percentile(delays, 0.99),
        max_interarrival_gap_ms=max(gaps, default=0),
        negative_clock_delay_events=sum(value < 0 for value in raw_delays),
        symbols=symbol_stats,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze NovaArb raw market-capture health")
    parser.add_argument("path", help="NovaArb JSONL or JSONL.GZ research capture")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    summary = summarize_capture(args.path)
    if args.json:
        print(json.dumps(asdict(summary), separators=(",", ":"), sort_keys=True))
        return

    print("NovaArb capture health")
    print(f"snapshots: {summary.snapshots}")
    print(f"duration: {summary.duration_ms} ms")
    print(f"event rate: {summary.events_per_second:.2f}/s")
    print(
        "feed delay p50/p95/p99: "
        f"{summary.median_feed_delay_ms}/{summary.p95_feed_delay_ms}/"
        f"{summary.p99_feed_delay_ms} ms"
    )
    print(f"max interarrival gap: {summary.max_interarrival_gap_ms} ms")
    print(f"negative clock-delay events: {summary.negative_clock_delay_events}")
    print("symbols:")
    for stats in summary.symbols:
        print(
            f"  {stats.symbol}: n={stats.snapshots} rate={stats.events_per_second:.2f}/s "
            f"delay_p95={stats.p95_feed_delay_ms}ms gap_max={stats.max_interarrival_gap_ms}ms"
        )


if __name__ == "__main__":
    main()
