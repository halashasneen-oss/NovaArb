from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.funding import FundingCarryConfig, FundingCarryScanner
from novaarb.research import ResearchRecorder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NovaArb public funding-carry research scanner")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
    )
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--intervals", type=int, default=1)
    parser.add_argument("--funding-haircut", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--spot-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--futures-fee-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--exit-reserve-bps", type=Decimal, default=Decimal("4"))
    parser.add_argument("--basis-risk-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--funding-refresh-seconds", type=int, default=60)
    parser.add_argument(
        "--record",
        default=None,
        help="research log (.jsonl or .jsonl.gz) with books, funding and evaluations",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = FundingCarryConfig(
        target_notional_usdt=args.notional,
        funding_intervals=args.intervals,
        funding_haircut=args.funding_haircut,
        spot_taker_fee_bps=args.spot_fee_bps,
        futures_taker_fee_bps=args.futures_fee_bps,
        exit_market_reserve_bps=args.exit_reserve_bps,
        basis_risk_reserve_bps=args.basis_risk_bps,
        min_net_edge_bps=args.min_edge_bps,
    )
    symbols = tuple(symbol.upper() for symbol in args.symbols)
    recorder = ResearchRecorder(args.record) if args.record else None
    if recorder is not None:
        recorder.append_metadata(
            "funding_session",
            {
                "symbols": symbols,
                "config": config,
                "funding_refresh_seconds": args.funding_refresh_seconds,
            },
        )
    scanner = FundingCarryScanner(
        symbols=symbols,
        config=config,
        funding_refresh_seconds=args.funding_refresh_seconds,
        recorder=recorder,
    )
    queue = await scanner.events()
    print("NovaArb funding carry scanner running (public data; research only).")
    print(
        f"symbols={','.join(scanner.symbols)} notional={config.target_notional_usdt} "
        f"intervals={config.funding_intervals} haircut={config.funding_haircut}"
    )
    while True:
        event = await queue.get()
        opportunity = event.opportunity
        print(
            f"{opportunity.symbol} funding={opportunity.funding_rate_bps:.3f}bps "
            f"basis={opportunity.current_basis_bps:.3f}bps "
            f"expected_net={opportunity.expected_net_profit_usdt:.6f} USDT "
            f"edge={opportunity.expected_net_edge_bps:.3f}bps"
        )


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb funding scanner stopped.")


if __name__ == "__main__":
    main()
