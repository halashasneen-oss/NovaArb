from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal

from novaarb.allocator import AllocationConfig
from novaarb.bybit import BybitPublicVenueAdapter
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.research import ResearchRecorder
from novaarb.shadow import MultiAssetInventoryLedger, ShadowRiskConfig
from novaarb.shadow_live import (
    ShadowLiveConfig,
    ShadowLiveCoordinator,
    run_public_shadow_session,
)
from novaarb.shadow_portfolio_cli import _parse_balances, _parse_instruments
from novaarb.shadow_scanner import (
    MultiInstrumentPublicShadowScanner,
    MultiInstrumentShadowEngine,
)
from novaarb.venue import BinancePublicVenueAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "NovaArb long-running Binance/Bybit public shadow operation; "
            "no authenticated orders or transfers"
        )
    )
    parser.add_argument(
        "--instrument",
        action="append",
        required=True,
        metavar="BASE/QUOTE",
        help="repeat for each Spot instrument, e.g. --instrument BTC/USDT",
    )
    parser.add_argument(
        "--balance",
        action="append",
        required=True,
        metavar="VENUE:ASSET:QUANTITY",
        help="hypothetical pre-funded inventory; repeat for each balance",
    )
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=500)
    parser.add_argument("--max-book-skew-ms", type=int, default=150)
    parser.add_argument("--binance-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--bybit-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("1"))
    parser.add_argument("--rebalance-reserve-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--capital", type=Decimal, default=Decimal("2000"))
    parser.add_argument("--cash-reserve", type=Decimal, default=Decimal("0"))
    parser.add_argument("--max-positions", type=int, default=4)
    parser.add_argument(
        "--max-capital-per-opportunity",
        type=Decimal,
        default=Decimal("300"),
    )
    parser.add_argument("--max-daily-loss", type=Decimal, default=Decimal("25"))
    parser.add_argument(
        "--max-venue-concentration",
        type=Decimal,
        default=Decimal("0.70"),
    )
    parser.add_argument(
        "--max-asset-concentration",
        type=Decimal,
        default=Decimal("0.80"),
    )
    parser.add_argument("--allocation-window-ms", type=int, default=100)
    parser.add_argument(
        "--execution-delay-ms",
        type=int,
        default=50,
        help="delay between candidate selection and hypothetical public-book fill",
    )
    parser.add_argument("--signal-cooldown-ms", type=int, default=1_000)
    parser.add_argument("--max-data-staleness-ms", type=int, default=2_000)
    parser.add_argument("--heartbeat-ms", type=int, default=5_000)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="optional foreground run duration; omit to run until interrupted",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="optional raw market/evaluation research capture (.jsonl or .jsonl.gz)",
    )
    parser.add_argument(
        "--metrics",
        default=None,
        help="optional heartbeat metrics research log (.jsonl or .jsonl.gz)",
    )
    return parser


def _costs(args: argparse.Namespace) -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile(
            venue="binance",
            taker_fee_bps=args.binance_fee_bps,
            execution_reserve_bps=args.execution_reserve_bps,
            rebalance_reserve_bps=args.rebalance_reserve_bps,
        ),
        VenueCostProfile(
            venue="bybit",
            taker_fee_bps=args.bybit_fee_bps,
            execution_reserve_bps=args.execution_reserve_bps,
            rebalance_reserve_bps=args.rebalance_reserve_bps,
        ),
    )


async def _run(args: argparse.Namespace) -> ShadowLiveCoordinator:
    instruments = _parse_instruments(args.instrument)
    balances = _parse_balances(args.balance)
    costs = _costs(args)
    inventory = MultiAssetInventoryLedger(balances)
    market_recorder = ResearchRecorder(args.record) if args.record else None
    metrics_recorder = ResearchRecorder(args.metrics) if args.metrics else None
    engine = MultiInstrumentShadowEngine(
        config=CrossVenueConfig(
            target_notional_quote=args.notional,
            min_net_edge_bps=args.min_edge_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        costs=costs,
        inventory=inventory,
        recorder=market_recorder,
    )
    scanner = MultiInstrumentPublicShadowScanner(
        engine=engine,
        adapters=(BinancePublicVenueAdapter(), BybitPublicVenueAdapter()),
        instruments=instruments,
        emit_cooldown_ms=args.signal_cooldown_ms,
    )
    coordinator = ShadowLiveCoordinator(
        engine=engine,
        costs=costs,
        quote_asset=args.quote,
        allocation_config=AllocationConfig(
            total_capital_usdt=args.capital,
            cash_reserve_usdt=args.cash_reserve,
            max_positions=args.max_positions,
            max_capital_per_opportunity_usdt=args.max_capital_per_opportunity,
            min_expected_edge_bps=args.min_edge_bps,
        ),
        risk_config=ShadowRiskConfig(
            max_daily_loss_quote=args.max_daily_loss,
            max_venue_concentration_pct=args.max_venue_concentration,
            max_asset_concentration_pct=args.max_asset_concentration,
        ),
        live_config=ShadowLiveConfig(
            allocation_window_ms=args.allocation_window_ms,
            execution_delay_ms=args.execution_delay_ms,
            heartbeat_interval_ms=args.heartbeat_ms,
            max_data_staleness_ms=args.max_data_staleness_ms,
        ),
    )
    await run_public_shadow_session(
        scanner=scanner,
        coordinator=coordinator,
        metrics_recorder=metrics_recorder,
        duration_seconds=args.duration_seconds,
    )
    return coordinator


def main() -> None:
    args = _parser().parse_args()
    print("NovaArb shadow operation starting: public market data, no live orders.")
    try:
        coordinator = asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb shadow operation stopped by operator.")
        return
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(coordinator.metrics(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
