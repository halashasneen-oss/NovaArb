from __future__ import annotations

import argparse

from novaarb.funding_replay import replay_funding_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a NovaArb funding-carry research log")
    parser.add_argument("path")
    parser.add_argument("--gap-tolerance-ms", type=int, default=5_000)
    args = parser.parse_args()

    summary = replay_funding_log(args.path, gap_tolerance_ms=args.gap_tolerance_ms)
    print("NovaArb funding replay summary")
    print(f"book snapshots: {summary.book_snapshots}")
    print(f"funding updates: {summary.funding_updates}")
    print(f"evaluations: {summary.evaluations}")
    print(f"approved observations: {summary.approved_observations}")
    print(f"opportunity windows: {summary.opportunity_windows}")
    print(f"median window: {summary.median_window_ms} ms")
    print(f"max projected edge: {summary.max_projected_edge_bps:.3f} bps")
    print(f"rejections: {summary.rejection_reasons}")
    print("symbols:")
    for stats in summary.symbols:
        print(
            f"  {stats.symbol}: evaluations={stats.evaluations} "
            f"approved={stats.approved_observations} windows={stats.windows} "
            f"max_edge={stats.max_projected_edge_bps:.3f}bps "
            f"max_funding={stats.max_funding_rate_bps:.3f}bps"
        )


if __name__ == "__main__":
    main()
