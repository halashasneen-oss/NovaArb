from __future__ import annotations

from collections import Counter, defaultdict
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
    LatencyProfile("stressed", 200, 200, 500),
)


def _median(values: list[Decimal | int]) -> Decimal:
    return Decimal(str(median(values))) if values else Decimal("0")


def _percentile(values: list[Decimal], fraction: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    raw_index = int((Decimal(len(ordered) - 1) * fraction).to_integral_value())
    index = max(0, min(len(ordered) - 1, raw_index))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class RouteLatencyStats:
    profile_name: str
    route_id: str
    detected_signals: int
    completed: int
    profitable: int
    failed: int
    total_net_profit: Decimal
    median_detected_edge_bps: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal

    @property
    def profitable_signal_rate(self) -> Decimal:
        if self.detected_signals == 0:
            return Decimal("0")
        return Decimal(self.profitable) / Decimal(self.detected_signals)


@dataclass(frozen=True, slots=True)
class LatencyReplayStats:
    profile_name: str
    first_leg_ms: int
    inter_leg_ms: int
    detected_signals: int
    completed: int
    profitable: int
    losing: int
    failed: int
    total_net_profit: Decimal
    median_detected_edge_bps: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal
    p90_edge_decay_bps: Decimal
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

    @property
    def profitable_signal_rate(self) -> Decimal:
        if self.detected_signals == 0:
            return Decimal("0")
        return Decimal(self.profitable) / Decimal(self.detected_signals)


@dataclass(frozen=True, slots=True)
class TriangleExecutionReplaySummary:
    snapshots: int
    approved_signals: int
    profiles: tuple[LatencyReplayStats, ...]
    route_stats: tuple[RouteLatencyStats, ...]


class _Accumulator:
    def __init__(self, profile: LatencyProfile) -> None:
        self.profile = profile
        self.results: list[SequentialFillResult] = []
        self.by_route: dict[str, list[SequentialFillResult]] = defaultdict(list)
        self.failures: Counter[str] = Counter()

    def add(self, result: SequentialFillResult) -> None:
        self.results.append(result)
        self.by_route[result.route_id].append(result)
        if not result.completed:
            self.failures[result.failure.value] += 1

    @staticmethod
    def _route_stats(
        profile_name: str,
        route_id: str,
        results: list[SequentialFillResult],
    ) -> RouteLatencyStats:
        completed = [result for result in results if result.completed]
        profitable = [result for result in completed if result.net_profit > 0]
        detected_edges = [result.detected_edge_bps for result in results]
        realized_edges = [result.realized_edge_bps for result in completed]
        decay = [result.edge_decay_bps for result in completed]
        return RouteLatencyStats(
            profile_name=profile_name,
            route_id=route_id,
            detected_signals=len(results),
            completed=len(completed),
            profitable=len(profitable),
            failed=len(results) - len(completed),
            total_net_profit=sum(
                (result.net_profit for result in completed),
                start=Decimal("0"),
            ),
            median_detected_edge_bps=_median(detected_edges),
            median_realized_edge_bps=_median(realized_edges),
            median_edge_decay_bps=_median(decay),
        )

    def finish(self) -> tuple[LatencyReplayStats, tuple[RouteLatencyStats, ...]]:
        completed = [result for result in self.results if result.completed]
        detected = [result.detected_edge_bps for result in self.results]
        realized = [result.realized_edge_bps for result in completed]
        decay = [result.edge_decay_bps for result in completed]
        durations = [result.duration_ms for result in completed]
        profitable = sum(result.net_profit > 0 for result in completed)
        losing = sum(result.net_profit <= 0 for result in completed)
        overall = LatencyReplayStats(
            profile_name=self.profile.name,
            first_leg_ms=self.profile.detection_to_first_leg_ms,
            inter_leg_ms=self.profile.inter_leg_ms,
            detected_signals=len(self.results),
            completed=len(completed),
            profitable=profitable,
            losing=losing,
            failed=len(self.results) - len(completed),
            total_net_profit=sum(
                (result.net_profit for result in completed),
                start=Decimal("0"),
            ),
            median_detected_edge_bps=_median(detected),
            median_realized_edge_bps=_median(realized),
            median_edge_decay_bps=_median(decay),
            p90_edge_decay_bps=_percentile(decay, Decimal("0.90")),
            median_duration_ms=_median(durations),
            failures=dict(sorted(self.failures.items())),
        )
        route_stats = tuple(
            sorted(
                (
                    self._route_stats(self.profile.name, route_id, results)
                    for route_id, results in self.by_route.items()
                ),
                key=lambda item: (
                    item.profitable_signal_rate,
                    item.total_net_profit,
                    item.detected_signals,
                ),
                reverse=True,
            )
        )
        return overall, route_stats


def replay_triangle_execution(
    path: str,
    *,
    profiles: tuple[LatencyProfile, ...] = DEFAULT_LATENCY_PROFILES,
) -> TriangleExecutionReplaySummary:
    if not profiles:
        raise ValueError("at least one latency profile is required")
    names = [profile.name for profile in profiles]
    if len(names) != len(set(names)):
        raise ValueError("latency profile names must be unique")

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
    accumulators = {profile.name: _Accumulator(profile) for profile in profiles}
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

    profile_stats: list[LatencyReplayStats] = []
    route_stats: list[RouteLatencyStats] = []
    for profile in profiles:
        overall, routes_for_profile = accumulators[profile.name].finish()
        profile_stats.append(overall)
        route_stats.extend(routes_for_profile)

    return TriangleExecutionReplaySummary(
        snapshots=len(snapshots),
        approved_signals=approved_signals,
        profiles=tuple(profile_stats),
        route_stats=tuple(route_stats),
    )
