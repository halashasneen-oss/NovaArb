from __future__ import annotations

import argparse
from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_replay import (
    CrossVenueLatencyProfile,
    CrossVenueReplayConfig,
    replay_cross_venue_capture,
)
from novaarb.inventory import RebalanceConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a Binance/Bybit cross-venue public-book capture"
    )
    parser.add_argument("path")
    parser.add_argument("--base", default="BTC")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--binance-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--bybit-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("1"))
    parser.add_argument("--rebalance-reserve-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--base-per-venue", type=Decimal, default=Decimal("1"))
    parser.add_argument("--quote-per-venue", type=Decimal, default=Decimal("1000"))
    parser.add_argument("--buy-latency-ms", type=int, default=50)
    parser.add_argument("--sell-latency-ms", type=int, default=50)
    parser.add_argument("--max-wait-ms", type=int, default=250)
    parser.add_argument("--route-cooldown-ms", type=int, default=1000)
    parser.add_argument("--base-transfer-cost-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--quote-transfer-cost-bps", type=Decimal, default=Decimal("2"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    reserve = args.execution_reserve_bps
    rebalance_reserve = args.rebalance_reserve_bps
    costs = (
        VenueCostProfile(
            "binance",
            args.binance_fee_bps,
            execution_reserve_bps=reserve,
            rebalance_reserve_bps=rebalance_reserve,
        ),
        VenueCostProfile(
            "bybit",
            args.bybit_fee_bps,
            execution_reserve_bps=reserve,
            rebalance_reserve_bps=rebalance_reserve,
        ),
    )
    inventories = (
        VenueInventory("binance", args.base_per_venue, args.quote_per_venue),
        VenueInventory("bybit", args.base_per_venue, args.quote_per_venue),
    )
    summary = replay_cross_venue_capture(
        args.path,
        base_asset=args.base,
        quote_asset=args.quote,
        costs=costs,
        inventories=inventories,
        strategy_config=CrossVenueConfig(
            target_notional_quote=args.notional,
            min_net_edge_bps=args.min_edge_bps,
        ),
        latency=CrossVenueLatencyProfile(
            buy_ms=args.buy_latency_ms,
            sell_ms=args.sell_latency_ms,
            max_wait_ms=args.max_wait_ms,
        ),
        replay=CrossVenueReplayConfig(route_cooldown_ms=args.route_cooldown_ms),
        rebalance_config=RebalanceConfig(
            base_transfer_cost_bps=args.base_transfer_cost_bps,
            quote_transfer_cost_bps=args.quote_transfer_cost_bps,
        ),
    )

    print("NovaArb cross-venue replay")
    print(
        f"signals={summary.detected_signals} completed={summary.completed_trades} "
        f"profitable={summary.profitable_trades} "
        f"inventory_rejections={summary.inventory_rejections} "
        f"missing_future_books={summary.missing_future_books}"
    )
    print(
        f"net={summary.total_net_profit_quote:.6f} {args.quote.upper()} "
        f"profitable_rate={summary.profitable_trade_rate:.2%}"
    )
    print(
        f"median detected={summary.median_detected_edge_bps:.3f}bps "
        f"realized={summary.median_realized_edge_bps:.3f}bps "
        f"decay={summary.median_edge_decay_bps:.3f}bps"
    )
    print(
        f"rebalance_cost={summary.rebalance_plan.total_estimated_cost_quote:.6f} "
        f"{args.quote.upper()} base_moved={summary.rebalance_plan.total_base_moved} "
        f"quote_moved={summary.rebalance_plan.total_quote_moved}"
    )


if __name__ == "__main__":
    main()
