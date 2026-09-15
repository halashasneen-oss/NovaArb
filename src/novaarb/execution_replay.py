from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from novaarb.execution import (
    BookTimeline,
    LatencyProfile,
    SequentialFillResult,
    SequentialTriangleSimulator,
)
from novaarb.research import iter_records, snapshot_from_record
from novaarb.triangle_replay import load_triangle_session
from novaarb.triangle_scanner import TriangularScanner


DEFAULT_LATENCY_PROFILES = (
    LatencyProfile("fast", 25, 25, 125),
    LatencyProfile("balanced", 50, 50, 200),
    LatencyProfile("slow", 100, 100, 300),
)


@dataclass(frozen=True, slots=True)
class LatencyReplayStats:
    profile_name: str
    detected_signals: int
    completed: int
    profitable: int
    losing: int
    failed: int
    total_net_profit: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal
    median_duration_ms: Decimal
    failures: dict[str, int]

    @property
    def completion_rate(self) -> Decimal:
        if self.detected_signals == 0:
            return Decimal("0")
        return Decimal(self.completed) / Decimal(self.detected_signals)

    @property
    def profitable_completion_rate(self) -> Decimal:
        if self.completed == 0:
            return Decimal("0")
        return Decimal(self.profitable) / Decimal(self.completed)


@dataclass(frozen=True, slots=True)
class TriangleExecutionReplaySummary:
    snapshots: int
    approved_signals: int
    profiles: tuple[LatencyReplayStats, ...]


class _Accumulator:
    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        self.results: list[SequentialFillResult] = []
        self.failures: Counter[str] = Counter()

    def add(self, result: SequentialFillResult) -> None:
        self.results.append(result)
        if not result.completed:
            self.failures[result.failure.value] += 1

    def finish(self) -> LatencyReplayStats:
        completed = [result for result in self.results if result.completed]
        realized = [result.realized_edge_bps for result in completed]
        decay = [result.edge_decay_bps for result in completed]
        durations = [result.duration_ms for result in completed]
        profitable = sum(result.net_profit > 0 for result in completed)
        losing = sum(result.net_profit <= 0 for result in completed)
        return LatencyReplayStats(
            profile_name=self.profile_name,
            detected_signals=len(self.results),
            completed=len(completed),
            profitable=profitable,
            losing=losing,
            failed=len(self.results) - len(completed),
            total_net_profit=sum(
                (result.net_profit for result in completed),
                start=Decimal("0"),
            ),
            median_realized_edge_bps=(
                Decimal(str(median(realized))) if realized else Decimal("0")
            ),
            median_edge_decay_bps=(
                Decimal(str(median(decay))) if decay else Decimal("0")
            ),
            median_duration_ms=(
                Decimal(str(median(durations))) if durations else Decimal("0")
            ),
            failures=dict(sorted(self.failures.items())),
        )


def replay_triangle_execution(
    path: str,
    *,
    profiles: tuple[LatencyProfile, ...] = DEFAULT_LATENCY_PROFILES,
) -> TriangleExecutionReplaySummary:
    if not profiles:
        raise ValueError("at least one latency profile is required")

    records = list(iter_records(path))
    rules, routes, config = load_triangle_session(records)
    snapshots = [
        snapshot_from_record(record)
        for record in records
        if record.get("kind") == "book"
    ]
    timeline = BookTimeline(snapshots)
    scanner = TriangularScanner(rules=rules, routes=routes, config=config)
    simulators = {
        profile.name: SequentialTriangleSimulator(
            rules=rules,
            taker_fee_bps=config.taker_fee_bps,
            latency=profile,
        )
        for profile in profiles
    }
    accumulators = {name: _Accumulator(name) for name in simulators}
    approved_signals = 0

    for snapshot in snapshots:
        events = scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms)
        for event in events:
            if not event.risk.approved:
                continue
            approved_signals += 1
            for name, simulator in simulators.items():
                result = simulator.simulate(event.opportunity, timeline)
                accumulators[name].add(result)

    return TriangleExecutionReplaySummary(
        snapshots=len(snapshots),
        approved_signals=approved_signals,
        profiles=tuple(accumulators[profile.name].finish() for profile in profiles),
    )
