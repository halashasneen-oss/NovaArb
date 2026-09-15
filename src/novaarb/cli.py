from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.research import ResearchRecorder, ReplayAccumulator, iter_records, snapshot_from_record
from novaarb.scanner import ScannerConfig, SpotPerpScanner


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    parser.add_argument("--notional", type=Decimal, default=Decimal("50"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=750)
    parser.add_argument("--latency-reserve-bps", type=Decimal, default=Decimal("0.75"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NovaArb research-first arbitrage engine")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="run public Binance spot/perpetual scanner")
    _add_strategy_args(scan)
    scan.add_argument(
        "--record",
        default=None,
        help="research log (.jsonl or .jsonl.gz); records raw books and evaluations",
    )

    replay = sub.add_parser("replay", help="deterministically replay a research log")
    replay.add_argument("path")
    replay.add_argument("--notional", type=Decimal, default=Decimal("50"))
    replay.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    replay.add_argument("--max-book-age-ms", type=int, default=750)
    replay.add_argument("--latency-reserve-bps", type=Decimal, default=Decimal("0.75"))
    replay.add_argument("--gap-tolerance-ms", type=int, default=500)
    return parser


def _config(args: argparse.Namespace, symbols: tuple[str, ...]) -> ScannerConfig:
    return ScannerConfig(
        symbols=symbols,
        target_notional_usd=args.notional,
        min_net_edge_bps=args.min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_notional_usd=max(args.notional, Decimal("100")),
        latency_reserve_bps=args.latency_reserve_bps,
    )


async def _scan(args: argparse.Namespace) -> None:
    recorder = ResearchRecorder(args.record) if args.record else None
    symbols = tuple(symbol.upper() for symbol in args.symbols)
    config = _config(args, symbols)
    scanner = SpotPerpScanner(config, recorder)
    queue = await scanner.events()
    print("NovaArb scanner running (research mode; no order endpoint exists).")
    print(f"symbols={','.join(config.symbols)} target_notional={config.target_notional_usd} USDT")
    while True:
        event = await queue.get()
        opp = event.opportunity
        print(
            f"{opp.symbol} buy={opp.buy_market.value} sell={opp.sell_market.value} "
            f"net={opp.costs.net_profit_usd:.6f} USDT "
            f"edge={opp.costs.net_edge_bps:.3f} bps "
            f"age={max(opp.buy_book_age_ms, opp.sell_book_age_ms)}ms"
        )


def _replay(args: argparse.Namespace) -> None:
    books = [
        snapshot_from_record(record)
        for record in iter_records(args.path)
        if record["kind"] == "book"
    ]
    symbols = tuple(sorted({book.symbol for book in books}))
    if not books:
        raise SystemExit("research log contains no book records")
    scanner = SpotPerpScanner(_config(args, symbols))
    accumulator = ReplayAccumulator(gap_tolerance_ms=args.gap_tolerance_ms)
    for snapshot in books:
        accumulator.add_snapshot(snapshot)
        events = scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms)
        accumulator.add_evaluations(events)
    summary = accumulator.finish()

    print("NovaArb replay summary")
    print(f"snapshots: {summary.snapshots}")
    print(f"symbols: {', '.join(summary.symbols)}")
    print(f"evaluations: {summary.evaluations}")
    print(f"approved observations: {summary.approved_observations}")
    print(f"opportunity windows: {summary.opportunity_windows}")
    print(f"max executable edge: {summary.max_edge_bps:.3f} bps")
    print(f"median window: {summary.median_window_ms} ms")
    print(f"median observations/window: {summary.median_window_observations}")
    print(f"median feed delay: {summary.median_feed_delay_ms} ms")
    print(f"rejections: {summary.rejection_reasons}")


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "scan":
            asyncio.run(_scan(args))
        else:
            _replay(args)
    except KeyboardInterrupt:
        print("\nNovaArb stopped.")


if __name__ == "__main__":
    main()
