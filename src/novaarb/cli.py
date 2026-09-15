from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.execution import LatencyProfile
from novaarb.execution_replay import replay_triangle_execution
from novaarb.paper import PaperPortfolioConfig, simulate_triangle_portfolio
from novaarb.research import ResearchRecorder, ReplayAccumulator, iter_records, snapshot_from_record
from novaarb.scanner import ScannerConfig, SpotPerpScanner
from novaarb.symbols import fetch_spot_exchange_info
from novaarb.triangle_replay import replay_triangle_log
from novaarb.triangle_scanner import TriangularScanner
from novaarb.triangular import TriangleConfig


def _add_basis_args(parser: argparse.ArgumentParser, *, include_symbols: bool) -> None:
    if include_symbols:
        parser.add_argument(
            "--symbols",
            nargs="+",
            default=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        )
    parser.add_argument("--notional", type=Decimal, default=Decimal("50"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=750)
    parser.add_argument("--max-book-skew-ms", type=int, default=250)
    parser.add_argument("--latency-reserve-bps", type=Decimal, default=Decimal("0.75"))
    parser.add_argument("--spot-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--futures-fee-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--exit-market-reserve-bps", type=Decimal, default=Decimal("2"))


def _add_latency_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--first-leg-ms", type=int, default=50)
    parser.add_argument("--inter-leg-ms", type=int, default=50)
    parser.add_argument("--max-book-wait-ms", type=int, default=200)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NovaArb research-first arbitrage engine")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="run public Binance spot/perpetual basis scanner")
    _add_basis_args(scan, include_symbols=True)
    scan.add_argument(
        "--record",
        default=None,
        help="research log (.jsonl or .jsonl.gz); records raw books and evaluations",
    )

    replay = sub.add_parser("replay", help="deterministically replay a basis research log")
    replay.add_argument("path")
    _add_basis_args(replay, include_symbols=False)
    replay.add_argument("--gap-tolerance-ms", type=int, default=500)

    triangle = sub.add_parser("triangle", help="run closed-cycle spot triangular scanner")
    triangle.add_argument("--anchor", default="USDT")
    triangle.add_argument(
        "--assets",
        nargs="+",
        default=[
            "BTC",
            "ETH",
            "BNB",
            "SOL",
            "XRP",
            "ADA",
            "DOGE",
            "LINK",
            "AVAX",
            "TRX",
            "LTC",
            "USDC",
            "FDUSD",
        ],
    )
    triangle.add_argument("--notional", type=Decimal, default=Decimal("100"))
    triangle.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    triangle.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("2"))
    triangle.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    triangle.add_argument("--max-book-age-ms", type=int, default=500)
    triangle.add_argument("--max-book-skew-ms", type=int, default=150)
    triangle.add_argument(
        "--record",
        default=None,
        help="research log (.jsonl or .jsonl.gz); records raw books and evaluations",
    )

    triangle_replay = sub.add_parser(
        "triangle-replay",
        help="replay a self-contained triangular research log",
    )
    triangle_replay.add_argument("path")
    triangle_replay.add_argument("--gap-tolerance-ms", type=int, default=300)
    triangle_replay.add_argument("--top-routes", type=int, default=10)

    execution_replay = sub.add_parser(
        "execution-replay",
        help="reprice approved triangle signals under multiple latency profiles",
    )
    execution_replay.add_argument("path")

    paper = sub.add_parser(
        "paper",
        help="run capital-aware sequential paper execution on a triangle research log",
    )
    paper.add_argument("path")
    _add_latency_args(paper)
    paper.add_argument("--initial-balance", type=Decimal, default=Decimal("1000"))
    paper.add_argument("--route-cooldown-ms", type=int, default=500)
    paper.add_argument("--recovery-delay-ms", type=int, default=50)
    paper.add_argument("--recovery-book-wait-ms", type=int, default=250)
    return parser


def _basis_config(args: argparse.Namespace, symbols: tuple[str, ...]) -> ScannerConfig:
    return ScannerConfig(
        symbols=symbols,
        target_notional_usd=args.notional,
        min_net_edge_bps=args.min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_book_skew_ms=args.max_book_skew_ms,
        max_notional_usd=max(args.notional, Decimal("100")),
        spot_taker_fee_bps=args.spot_fee_bps,
        futures_taker_fee_bps=args.futures_fee_bps,
        latency_reserve_bps=args.latency_reserve_bps,
        exit_market_reserve_bps=args.exit_market_reserve_bps,
    )


async def _scan(args: argparse.Namespace) -> None:
    recorder = ResearchRecorder(args.record) if args.record else None
    symbols = tuple(symbol.upper() for symbol in args.symbols)
    config = _basis_config(args, symbols)
    if recorder is not None:
        recorder.append_metadata("basis_config", config)
    scanner = SpotPerpScanner(config, recorder)
    queue = await scanner.events()
    print("NovaArb basis scanner running (research mode; no order endpoint exists).")
    print(f"symbols={','.join(config.symbols)} target_notional={config.target_notional_usd} USDT")
    while True:
        event = await queue.get()
        opp = event.opportunity
        print(
            f"{opp.symbol} buy={opp.buy_market.value} sell={opp.sell_market.value} "
            f"capture={opp.costs.net_capture_usd:.6f} USDT "
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
    scanner = SpotPerpScanner(_basis_config(args, symbols))
    accumulator = ReplayAccumulator(gap_tolerance_ms=args.gap_tolerance_ms)
    for snapshot in books:
        accumulator.add_snapshot(snapshot)
        events = scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms)
        accumulator.add_evaluations(events)
    summary = accumulator.finish()

    print("NovaArb basis replay summary")
    print(f"snapshots: {summary.snapshots}")
    print(f"symbols: {', '.join(summary.symbols)}")
    print(f"evaluations: {summary.evaluations}")
    print(f"approved observations: {summary.approved_observations}")
    print(f"opportunity windows: {summary.opportunity_windows}")
    print(f"max capture edge: {summary.max_edge_bps:.3f} bps")
    print(f"median window: {summary.median_window_ms} ms")
    print(f"median observations/window: {summary.median_window_observations}")
    print(f"median feed delay: {summary.median_feed_delay_ms} ms")
    print(f"rejections: {summary.rejection_reasons}")


async def _triangle(args: argparse.Namespace) -> None:
    recorder = ResearchRecorder(args.record) if args.record else None
    all_rules = await asyncio.to_thread(fetch_spot_exchange_info)
    anchor = args.anchor.upper()
    allowed_assets = {anchor, *(asset.upper() for asset in args.assets)}
    config = TriangleConfig(
        starting_amount=args.notional,
        taker_fee_bps=args.fee_bps,
        execution_reserve_bps=args.execution_reserve_bps,
        min_net_edge_bps=args.min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_book_skew_ms=args.max_book_skew_ms,
    )
    scanner = TriangularScanner.plan(
        rules=all_rules,
        anchor_asset=anchor,
        allowed_assets=allowed_assets,
        config=config,
        recorder=recorder,
    )
    if recorder is not None:
        recorder.append_metadata(
            "triangle_session",
            {
                "anchor": anchor,
                "allowed_assets": sorted(allowed_assets),
                "config": config,
                "rules": scanner.rules,
                "routes": scanner.routes,
            },
        )

    queue = await scanner.events()
    print("NovaArb triangular scanner running (research mode; no order endpoint exists).")
    print(
        f"anchor={anchor} routes={len(scanner.routes)} symbols={len(scanner.symbols)} "
        f"starting_amount={config.starting_amount} {anchor}"
    )
    while True:
        event = await queue.get()
        opp = event.opportunity
        print(
            f"{opp.route.route_id} final={opp.net_final_amount:.8f} {anchor} "
            f"net={opp.net_profit:.8f} edge={opp.net_edge_bps:.3f} bps "
            f"age={opp.max_book_age_ms}ms skew={opp.book_skew_ms}ms"
        )


def _triangle_replay(args: argparse.Namespace) -> None:
    summary = replay_triangle_log(
        args.path,
        gap_tolerance_ms=args.gap_tolerance_ms,
        top_n=args.top_routes,
    )
    print("NovaArb triangular replay summary")
    print(f"snapshots: {summary.snapshots}")
    print(f"evaluations: {summary.evaluations}")
    print(f"approved observations: {summary.approved_observations}")
    print(f"opportunity windows: {summary.opportunity_windows}")
    print(f"max edge: {summary.max_edge_bps:.3f} bps")
    print(f"median window: {summary.median_window_ms} ms")
    print(f"rejections: {summary.rejection_reasons}")
    print("top routes:")
    for route in summary.top_routes:
        print(
            f"  {route.route_id}: approved={route.approved_observations} "
            f"windows={route.windows} max_edge={route.max_edge_bps:.3f}bps"
        )


def _execution_replay(args: argparse.Namespace) -> None:
    summary = replay_triangle_execution(args.path)
    print("NovaArb sequential execution replay")
    print(f"snapshots: {summary.snapshots}")
    print(f"approved signals: {summary.approved_signals}")
    for stats in summary.profiles:
        print(
            f"{stats.profile_name}: complete={stats.completed}/{stats.detected_signals} "
            f"profitable={stats.profitable} losing={stats.losing} failed={stats.failed} "
            f"median_edge={stats.median_realized_edge_bps:.3f}bps "
            f"median_decay={stats.median_edge_decay_bps:.3f}bps "
            f"net={stats.total_net_profit:.8f}"
        )


def _paper(args: argparse.Namespace) -> None:
    latency = LatencyProfile(
        "cli",
        args.first_leg_ms,
        args.inter_leg_ms,
        args.max_book_wait_ms,
    )
    config = PaperPortfolioConfig(
        initial_balance=args.initial_balance,
        route_cooldown_ms=args.route_cooldown_ms,
        attempt_recovery=True,
        halt_on_unrecovered_leg_risk=True,
        recovery_delay_ms=args.recovery_delay_ms,
        recovery_book_wait_ms=args.recovery_book_wait_ms,
    )
    summary = simulate_triangle_portfolio(args.path, latency=latency, portfolio=config)
    print("NovaArb paper portfolio summary")
    print(f"trades: {summary.trades}")
    print(f"wins/losses: {summary.profitable_trades}/{summary.losing_trades}")
    print(f"initial/final: {summary.initial_balance} -> {summary.final_balance}")
    print(f"net: {summary.total_net_profit} return={summary.return_pct:.4f}%")
    print(f"max drawdown: {summary.max_drawdown_pct:.4f}%")
    print(f"profit factor: {summary.profit_factor}")
    print(
        f"execution failures={summary.failed_executions} "
        f"recovered={summary.recovered_exposures} "
        f"unrecovered={summary.unrecovered_exposures}"
    )
    print(f"halted_on_leg_risk: {summary.halted_on_leg_risk}")


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "scan":
            asyncio.run(_scan(args))
        elif args.command == "triangle":
            asyncio.run(_triangle(args))
        elif args.command == "triangle-replay":
            _triangle_replay(args)
        elif args.command == "execution-replay":
            _execution_replay(args)
        elif args.command == "paper":
            _paper(args)
        else:
            _replay(args)
    except KeyboardInterrupt:
        print("\nNovaArb stopped.")


if __name__ == "__main__":
    main()
