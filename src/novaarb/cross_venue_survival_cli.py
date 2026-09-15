from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.cross_venue_survival import analyze_cross_venue_survival


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure persistence of public cross-venue arbitrage signals"
    )
    parser.add_argument("capture")
    parser.add_argument("--base", default="BTC")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=500)
    parser.add_argument("--max-book-skew-ms", type=int, default=150)
    parser.add_argument("--binance-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--bybit-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("1"))
    parser.add_argument("--rebalance-reserve-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--gap-tolerance-ms", type=int, default=250)
    parser.add_argument(
        "--threshold-ms",
        action="append",
        type=int,
        dest="thresholds_ms",
        help="repeat to override default survival thresholds",
    )
    parser.add_argument("--output", default=None, help="optional JSON output path")
    return parser


def main() -> None:
    args = _parser().parse_args()
    thresholds = (
        tuple(args.thresholds_ms)
        if args.thresholds_ms
        else (0, 25, 50, 100, 200, 500, 1_000)
    )
    report = analyze_cross_venue_survival(
        args.capture,
        base_asset=args.base,
        quote_asset=args.quote,
        costs=(
            VenueCostProfile(
                "binance",
                args.binance_fee_bps,
                args.execution_reserve_bps,
                args.rebalance_reserve_bps,
            ),
            VenueCostProfile(
                "bybit",
                args.bybit_fee_bps,
                args.execution_reserve_bps,
                args.rebalance_reserve_bps,
            ),
        ),
        strategy_config=CrossVenueConfig(
            target_notional_quote=args.notional,
            min_net_edge_bps=args.min_edge_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        gap_tolerance_ms=args.gap_tolerance_ms,
        thresholds_ms=thresholds,
    )
    encoded = json.dumps(asdict(report), indent=2, sort_keys=True, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
