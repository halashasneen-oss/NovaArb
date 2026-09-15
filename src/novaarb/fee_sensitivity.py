from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from decimal import Decimal
from statistics import median

from novaarb.execution import BookTimeline, LatencyProfile, SequentialTriangleSimulator
from novaarb.research import iter_records, snapshot_from_record
from novaarb.triangle_replay import load_triangle_session
from novaarb.triangle_scanner import TriangularScanner


DEFAULT_FEE_TIERS_BPS = (
    Decimal("10"),
    Decimal("7.5"),
    Decimal("5"),
    Decimal("2.5"),
)
DEFAULT_FEE_LATENCY = LatencyProfile("fee-sensitivity", 50, 50, 200)


@dataclass(frozen=True, slots=True)
class FeeTierStats:
    taker_fee_bps: Decimal
    theoretical_signals: int
    completed: int
    profitable: int
    failed: int
    total_net_profit: Decimal
    median_detected_edge_bps: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal

    @property
    def profitable_signal_rate(self) -> Decimal:
        if self.theoretical_signals == 0:
            return Decimal("0")
        return Decimal(self.profitable) / Decimal(self.theoretical_signals)


@dataclass(frozen=True, slots=True)
class FeeSensitivitySummary:
    snapshots: int
    latency: LatencyProfile
    tiers: tuple[FeeTierStats, ...]


def _median_decimal(values: list[Decimal]) -> Decimal:
    return Decimal(str(median(values))) if values else Decimal("0")


def replay_fee_sensitivity(
    path: str,
    *,
    fee_tiers_bps: tuple[Decimal, ...] = DEFAULT_FEE_TIERS_BPS,
    latency: LatencyProfile = DEFAULT_FEE_LATENCY,
) -> FeeSensitivitySummary:
    if not fee_tiers_bps:
        raise ValueError("at least one fee tier is required")
    if any(fee < 0 for fee in fee_tiers_bps):
        raise ValueError("fee tiers cannot be negative")
    if len(fee_tiers_bps) != len(set(fee_tiers_bps)):
        raise ValueError("fee tiers must be unique")

    records = list(iter_records(path))
    rules, routes, base_config = load_triangle_session(records)
    snapshots = [
        snapshot_from_record(record)
        for record in records
        if record.get("kind") == "book"
    ]
    timeline = BookTimeline(snapshots)

    output: list[FeeTierStats] = []
    for fee in fee_tiers_bps:
        config = replace(base_config, taker_fee_bps=fee)
        scanner = TriangularScanner(rules=rules, routes=routes, config=config)
        simulator = SequentialTriangleSimulator(
            rules=rules,
            taker_fee_bps=fee,
            latency=latency,
        )
        theoretical = 0
        completed = 0
        profitable = 0
        failed = 0
        total_net_profit = Decimal("0")
        detected_edges: list[Decimal] = []
        realized_edges: list[Decimal] = []
        decay_edges: list[Decimal] = []

        for snapshot in snapshots:
            for event in scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms):
                if not event.risk.approved:
                    continue
                theoretical += 1
                detected_edges.append(event.opportunity.net_edge_bps)
                result = simulator.simulate(event.opportunity, timeline)
                if not result.completed:
                    failed += 1
                    continue
                completed += 1
                total_net_profit += result.net_profit
                realized_edges.append(result.realized_edge_bps)
                decay_edges.append(result.edge_decay_bps)
                if result.net_profit > 0:
                    profitable += 1

        output.append(
            FeeTierStats(
                taker_fee_bps=fee,
                theoretical_signals=theoretical,
                completed=completed,
                profitable=profitable,
                failed=failed,
                total_net_profit=total_net_profit,
                median_detected_edge_bps=_median_decimal(detected_edges),
                median_realized_edge_bps=_median_decimal(realized_edges),
                median_edge_decay_bps=_median_decimal(decay_edges),
            )
        )

    return FeeSensitivitySummary(
        snapshots=len(snapshots),
        latency=latency,
        tiers=tuple(output),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay NovaArb under alternate taker-fee tiers")
    parser.add_argument("path", help="self-contained triangular JSONL or JSONL.GZ capture")
    parser.add_argument(
        "--fees-bps",
        nargs="+",
        type=Decimal,
        default=list(DEFAULT_FEE_TIERS_BPS),
    )
    parser.add_argument("--first-leg-ms", type=int, default=50)
    parser.add_argument("--inter-leg-ms", type=int, default=50)
    parser.add_argument("--max-book-wait-ms", type=int, default=200)
    args = parser.parse_args()

    summary = replay_fee_sensitivity(
        args.path,
        fee_tiers_bps=tuple(args.fees_bps),
        latency=LatencyProfile(
            "cli-fee-sensitivity",
            args.first_leg_ms,
            args.inter_leg_ms,
            args.max_book_wait_ms,
        ),
    )
    print("NovaArb fee-tier sensitivity")
    print(f"snapshots: {summary.snapshots}")
    for tier in summary.tiers:
        print(
            f"fee={tier.taker_fee_bps}bps signals={tier.theoretical_signals} "
            f"complete={tier.completed} profitable={tier.profitable} failed={tier.failed} "
            f"median_detected={tier.median_detected_edge_bps:.3f}bps "
            f"median_realized={tier.median_realized_edge_bps:.3f}bps "
            f"net={tier.total_net_profit}"
        )


if __name__ == "__main__":
    main()
