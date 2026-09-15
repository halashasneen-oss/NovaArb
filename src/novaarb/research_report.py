from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from novaarb.capture_health import CaptureHealthSummary, summarize_capture
from novaarb.execution import LatencyProfile
from novaarb.execution_replay import (
    DEFAULT_LATENCY_PROFILES,
    RouteLatencyStats,
    replay_triangle_execution,
)
from novaarb.paper import PaperPortfolioConfig, simulate_triangle_portfolio


DEFAULT_INITIAL_BALANCE = Decimal("1000")


@dataclass(frozen=True, slots=True)
class ProfileEvidence:
    profile_name: str
    first_leg_ms: int
    inter_leg_ms: int
    detected_signals: int
    completed: int
    profitable: int
    profitable_signal_rate: Decimal
    total_execution_profit: Decimal
    median_detected_edge_bps: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal
    p90_edge_decay_bps: Decimal
    paper_trades: int
    paper_net_profit: Decimal
    paper_return_pct: Decimal
    paper_max_drawdown_pct: Decimal
    paper_profit_factor: Decimal
    recovered_exposures: int
    unrecovered_exposures: int
    halted_on_leg_risk: bool


@dataclass(frozen=True, slots=True)
class TriangleResearchReport:
    capture: CaptureHealthSummary
    approved_signals: int
    profiles: tuple[ProfileEvidence, ...]
    top_routes: tuple[RouteLatencyStats, ...]


def build_triangle_report(
    path: str,
    *,
    initial_balance: Decimal = DEFAULT_INITIAL_BALANCE,
    route_cooldown_ms: int = 500,
    profiles: tuple[LatencyProfile, ...] = DEFAULT_LATENCY_PROFILES,
    top_routes: int = 10,
) -> TriangleResearchReport:
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if route_cooldown_ms < 0:
        raise ValueError("route_cooldown_ms cannot be negative")
    if top_routes < 0:
        raise ValueError("top_routes cannot be negative")

    capture = summarize_capture(path)
    execution = replay_triangle_execution(path, profiles=profiles)
    profile_stats = {stats.profile_name: stats for stats in execution.profiles}

    evidence: list[ProfileEvidence] = []
    for profile in profiles:
        stats = profile_stats[profile.name]
        paper = simulate_triangle_portfolio(
            path,
            latency=profile,
            portfolio=PaperPortfolioConfig(
                initial_balance=initial_balance,
                route_cooldown_ms=route_cooldown_ms,
            ),
        )
        evidence.append(
            ProfileEvidence(
                profile_name=profile.name,
                first_leg_ms=profile.detection_to_first_leg_ms,
                inter_leg_ms=profile.inter_leg_ms,
                detected_signals=stats.detected_signals,
                completed=stats.completed,
                profitable=stats.profitable,
                profitable_signal_rate=stats.profitable_signal_rate,
                total_execution_profit=stats.total_net_profit,
                median_detected_edge_bps=stats.median_detected_edge_bps,
                median_realized_edge_bps=stats.median_realized_edge_bps,
                median_edge_decay_bps=stats.median_edge_decay_bps,
                p90_edge_decay_bps=stats.p90_edge_decay_bps,
                paper_trades=paper.trades,
                paper_net_profit=paper.total_net_profit,
                paper_return_pct=paper.return_pct,
                paper_max_drawdown_pct=paper.max_drawdown_pct,
                paper_profit_factor=paper.profit_factor,
                recovered_exposures=paper.recovered_exposures,
                unrecovered_exposures=paper.unrecovered_exposures,
                halted_on_leg_risk=paper.halted_on_leg_risk,
            )
        )

    ranked_routes = sorted(
        execution.route_stats,
        key=lambda item: (
            item.profitable_signal_rate,
            item.total_net_profit,
            item.detected_signals,
        ),
        reverse=True,
    )
    return TriangleResearchReport(
        capture=capture,
        approved_signals=execution.approved_signals,
        profiles=tuple(evidence),
        top_routes=tuple(ranked_routes[:top_routes]),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def report_json(report: TriangleResearchReport) -> str:
    return json.dumps(
        asdict(report),
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a consolidated NovaArb research report")
    parser.add_argument("path", help="self-contained triangular JSONL or JSONL.GZ capture")
    parser.add_argument("--initial-balance", type=Decimal, default=DEFAULT_INITIAL_BALANCE)
    parser.add_argument("--route-cooldown-ms", type=int, default=500)
    parser.add_argument("--top-routes", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    report = build_triangle_report(
        args.path,
        initial_balance=args.initial_balance,
        route_cooldown_ms=args.route_cooldown_ms,
        top_routes=args.top_routes,
    )
    if args.json:
        print(report_json(report))
        return

    print("NovaArb research evidence report")
    print(
        f"capture: {report.capture.snapshots} books over {report.capture.duration_ms}ms, "
        f"delay p50/p95/p99={report.capture.median_feed_delay_ms}/"
        f"{report.capture.p95_feed_delay_ms}/{report.capture.p99_feed_delay_ms}ms"
    )
    print(f"approved theoretical signals: {report.approved_signals}")
    print("latency evidence:")
    for profile in report.profiles:
        print(
            f"  {profile.profile_name} ({profile.first_leg_ms}+{profile.inter_leg_ms}ms): "
            f"profitable={profile.profitable}/{profile.detected_signals} "
            f"median realized={profile.median_realized_edge_bps:.3f}bps "
            f"median decay={profile.median_edge_decay_bps:.3f}bps "
            f"paper net={profile.paper_net_profit} drawdown={profile.paper_max_drawdown_pct:.4f}%"
        )
    print("top route/profile combinations:")
    for route in report.top_routes:
        print(
            f"  {route.profile_name} {route.route_id}: "
            f"profitable={route.profitable}/{route.detected_signals} "
            f"net={route.total_net_profit} median_decay={route.median_edge_decay_bps:.3f}bps"
        )


if __name__ == "__main__":
    main()
