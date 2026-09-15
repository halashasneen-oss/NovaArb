from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_replay import CrossVenueLatencyProfile, CrossVenueReplayConfig
from novaarb.cross_venue_sensitivity import run_cross_venue_sensitivity_matrix


def _decimal_csv(value: str) -> tuple[Decimal, ...]:
    values = tuple(Decimal(item.strip()) for item in value.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected at least one decimal value")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay cross-venue evidence across fee and rebalance-cost assumptions"
    )
    parser.add_argument("capture")
    parser.add_argument("--base", default="BTC")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=500)
    parser.add_argument("--max-book-skew-ms", type=int, default=150)
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("1"))
    parser.add_argument("--rebalance-reserve-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument(
        "--fee-tiers-bps",
        type=_decimal_csv,
        default=(Decimal("1"), Decimal("5"), Decimal("10")),
        help="comma-separated per-venue taker fee tiers, e.g. 1,5,10",
    )
    parser.add_argument(
        "--transfer-costs-bps",
        type=_decimal_csv,
        default=(Decimal("0"), Decimal("2"), Decimal("5"), Decimal("10")),
        help="comma-separated modeled inventory transfer costs",
    )
    parser.add_argument("--base-per-venue", type=Decimal, default=Decimal("1"))
    parser.add_argument("--quote-per-venue", type=Decimal, default=Decimal("1000"))
    parser.add_argument("--buy-latency-ms", type=int, default=50)
    parser.add_argument("--sell-latency-ms", type=int, default=50)
    parser.add_argument("--max-wait-ms", type=int, default=250)
    parser.add_argument("--route-cooldown-ms", type=int, default=1_000)
    parser.add_argument("--output", default=None, help="optional JSON output path")
    return parser


def main() -> None:
    args = _parser().parse_args()
    base_costs = (
        VenueCostProfile(
            "binance",
            Decimal("0"),
            args.execution_reserve_bps,
            args.rebalance_reserve_bps,
        ),
        VenueCostProfile(
            "bybit",
            Decimal("0"),
            args.execution_reserve_bps,
            args.rebalance_reserve_bps,
        ),
    )
    report = run_cross_venue_sensitivity_matrix(
        args.capture,
        base_asset=args.base,
        quote_asset=args.quote,
        base_costs=base_costs,
        inventories=(
            VenueInventory("binance", args.base_per_venue, args.quote_per_venue),
            VenueInventory("bybit", args.base_per_venue, args.quote_per_venue),
        ),
        fee_tiers_bps=args.fee_tiers_bps,
        transfer_costs_bps=args.transfer_costs_bps,
        strategy_config=CrossVenueConfig(
            target_notional_quote=args.notional,
            min_net_edge_bps=args.min_edge_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        latency=CrossVenueLatencyProfile(
            buy_ms=args.buy_latency_ms,
            sell_ms=args.sell_latency_ms,
            max_wait_ms=args.max_wait_ms,
        ),
        replay=CrossVenueReplayConfig(route_cooldown_ms=args.route_cooldown_ms),
    )
    encoded = json.dumps(asdict(report), indent=2, sort_keys=True, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
