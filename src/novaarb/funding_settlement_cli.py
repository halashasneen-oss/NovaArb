from __future__ import annotations

import argparse

from novaarb.funding_settlement_replay import (
    FundingSettlementReplayConfig,
    replay_funding_settlements,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Replay approved funding carry entries through captured settlement timestamps "
            "and observed market exits"
        )
    )
    parser.add_argument("path")
    parser.add_argument("--max-exit-wait-ms", type=int, default=60_000)
    parser.add_argument("--max-exit-book-skew-ms", type=int, default=1_000)
    parser.add_argument("--entry-cooldown-ms", type=int, default=5_000)
    args = parser.parse_args()

    summary = replay_funding_settlements(
        args.path,
        replay_config=FundingSettlementReplayConfig(
            max_exit_wait_ms=args.max_exit_wait_ms,
            max_exit_book_skew_ms=args.max_exit_book_skew_ms,
            entry_cooldown_ms=args.entry_cooldown_ms,
        ),
    )
    print("NovaArb funding settlement replay")
    print(f"candidate entries: {summary.candidate_entries}")
    print(f"attempted positions: {summary.attempted_positions}")
    print(f"completed trades: {summary.completed_trades}")
    print(f"profitable trades: {summary.profitable_trades}")
    print(f"profitable rate: {summary.profitable_trade_rate}")
    print(f"total modeled-realized net: {summary.total_net_profit_quote} USDT")
    print(f"median net edge: {summary.median_net_edge_bps} bps")
    print(f"median holding: {summary.median_holding_ms} ms")
    print(f"incomplete reasons: {summary.incomplete_reasons}")
    if summary.trades:
        print("trades:")
    for trade in summary.trades:
        print(
            f"  {trade.symbol} entry={trade.entry_time_ms} exit={trade.exit_time_ms} "
            f"settlements={trade.settlement_count} funding={trade.funding_quote:.8f} "
            f"net={trade.net_profit_quote:.8f} edge={trade.net_edge_bps:.3f}bps"
        )


if __name__ == "__main__":
    main()
