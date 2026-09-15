from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any

from novaarb.research import iter_records, snapshot_from_record
from novaarb.symbols import SymbolRules
from novaarb.triangle_scanner import TriangleScannerEvent, TriangularScanner
from novaarb.triangular import (
    TriangleConfig,
    TriangleRiskReason,
    TriangleRoute,
)


@dataclass(slots=True)
class TriangleWindow:
    route_id: str
    started_ms: int
    last_seen_ms: int
    observations: int
    max_edge_bps: Decimal

    @property
    def duration_ms(self) -> int:
        return max(0, self.last_seen_ms - self.started_ms)


class TriangleWindowTracker:
    def __init__(self, *, gap_tolerance_ms: int = 300) -> None:
        self.gap_tolerance_ms = gap_tolerance_ms
        self.active: dict[str, TriangleWindow] = {}
        self.closed: list[TriangleWindow] = []

    def observe(self, event: TriangleScannerEvent) -> None:
        route_id = event.opportunity.route.route_id
        timestamp = event.opportunity.created_time_ms
        current = self.active.get(route_id)
        if not event.risk.approved:
            if current is not None:
                self.closed.append(current)
                del self.active[route_id]
            return

        if current is not None and timestamp - current.last_seen_ms <= self.gap_tolerance_ms:
            current.last_seen_ms = timestamp
            current.observations += 1
            current.max_edge_bps = max(
                current.max_edge_bps,
                event.opportunity.net_edge_bps,
            )
            return

        if current is not None:
            self.closed.append(current)
        self.active[route_id] = TriangleWindow(
            route_id=route_id,
            started_ms=timestamp,
            last_seen_ms=timestamp,
            observations=1,
            max_edge_bps=event.opportunity.net_edge_bps,
        )

    def finish(self) -> tuple[TriangleWindow, ...]:
        self.closed.extend(self.active.values())
        self.active.clear()
        return tuple(self.closed)


@dataclass(frozen=True, slots=True)
class RouteReplayStats:
    route_id: str
    evaluations: int
    approved_observations: int
    windows: int
    max_edge_bps: Decimal


@dataclass(frozen=True, slots=True)
class TriangleReplaySummary:
    snapshots: int
    evaluations: int
    approved_observations: int
    opportunity_windows: int
    max_edge_bps: Decimal
    median_window_ms: Decimal
    rejection_reasons: dict[str, int]
    top_routes: tuple[RouteReplayStats, ...]


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _parse_rule(payload: dict[str, Any]) -> SymbolRules:
    return SymbolRules(
        symbol=str(payload["symbol"]),
        base_asset=str(payload["base_asset"]),
        quote_asset=str(payload["quote_asset"]),
        status=str(payload["status"]),
        step_size=_decimal(payload["step_size"]),
        min_quantity=_decimal(payload["min_quantity"]),
        min_notional=_decimal(payload["min_notional"]),
        market_step_size=_decimal(payload.get("market_step_size", "0")),
        market_min_quantity=_decimal(payload.get("market_min_quantity", "0")),
    )


def _parse_route(payload: dict[str, Any]) -> TriangleRoute:
    symbols = tuple(str(value) for value in payload["symbols"])
    if len(symbols) != 3:
        raise ValueError("triangle metadata route must contain exactly three symbols")
    return TriangleRoute(
        anchor_asset=str(payload["anchor_asset"]),
        first_asset=str(payload["first_asset"]),
        second_asset=str(payload["second_asset"]),
        symbols=(symbols[0], symbols[1], symbols[2]),
    )


def _parse_config(payload: dict[str, Any]) -> TriangleConfig:
    return TriangleConfig(
        starting_amount=_decimal(payload["starting_amount"]),
        taker_fee_bps=_decimal(payload["taker_fee_bps"]),
        execution_reserve_bps=_decimal(payload["execution_reserve_bps"]),
        min_net_edge_bps=_decimal(payload["min_net_edge_bps"]),
        max_book_age_ms=int(payload["max_book_age_ms"]),
        max_book_skew_ms=int(payload.get("max_book_skew_ms", 150)),
    )


def load_triangle_session(
    records: Iterable[dict[str, Any]],
) -> tuple[tuple[SymbolRules, ...], tuple[TriangleRoute, ...], TriangleConfig]:
    for record in records:
        if record.get("kind") != "metadata" or record.get("name") != "triangle_session":
            continue
        payload = record["payload"]
        rules = tuple(_parse_rule(item) for item in payload["rules"])
        routes = tuple(_parse_route(item) for item in payload["routes"])
        config = _parse_config(payload["config"])
        return rules, routes, config
    raise ValueError("triangle_session metadata not found in research log")


def replay_triangle_log(
    path: str,
    *,
    gap_tolerance_ms: int = 300,
    top_n: int = 10,
) -> TriangleReplaySummary:
    records = list(iter_records(path))
    rules, routes, config = load_triangle_session(records)
    scanner = TriangularScanner(rules=rules, routes=routes, config=config)
    tracker = TriangleWindowTracker(gap_tolerance_ms=gap_tolerance_ms)
    rejections: Counter[str] = Counter()
    route_evaluations: Counter[str] = Counter()
    route_approved: Counter[str] = Counter()
    route_max_edge: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    snapshots = 0
    evaluations = 0
    approved = 0
    max_edge = Decimal("0")

    for record in records:
        if record.get("kind") != "book":
            continue
        snapshot = snapshot_from_record(record)
        snapshots += 1
        for event in scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms):
            evaluations += 1
            route_id = event.opportunity.route.route_id
            route_evaluations[route_id] += 1
            max_edge = max(max_edge, event.opportunity.net_edge_bps)
            route_max_edge[route_id] = max(
                route_max_edge[route_id],
                event.opportunity.net_edge_bps,
            )
            if event.risk.approved:
                approved += 1
                route_approved[route_id] += 1
            else:
                reason = event.risk.reason
                key = reason.value if isinstance(reason, TriangleRiskReason) else str(reason)
                rejections[key] += 1
            tracker.observe(event)

    windows = tracker.finish()
    window_counts: Counter[str] = Counter(window.route_id for window in windows)
    durations = [window.duration_ms for window in windows]
    route_ids = set(route_evaluations) | set(window_counts)
    stats = [
        RouteReplayStats(
            route_id=route_id,
            evaluations=route_evaluations[route_id],
            approved_observations=route_approved[route_id],
            windows=window_counts[route_id],
            max_edge_bps=route_max_edge[route_id],
        )
        for route_id in route_ids
    ]
    stats.sort(
        key=lambda item: (item.approved_observations, item.max_edge_bps),
        reverse=True,
    )

    return TriangleReplaySummary(
        snapshots=snapshots,
        evaluations=evaluations,
        approved_observations=approved,
        opportunity_windows=len(windows),
        max_edge_bps=max_edge,
        median_window_ms=(
            Decimal(str(median(durations))) if durations else Decimal("0")
        ),
        rejection_reasons=dict(sorted(rejections.items())),
        top_routes=tuple(stats[:top_n]),
    )
