from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal

from novaarb.allocator import AllocationConfig
from novaarb.bybit import BybitPublicVenueAdapter
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.funding import FundingCarryConfig, FundingCarryScanner
from novaarb.funding_shadow import FundingShadowConfig
from novaarb.operator_metrics import TelemetryResearchRecorder, stream_telemetry_payload
from novaarb.research import ResearchRecorder
from novaarb.shadow import MultiAssetInventoryLedger, ShadowRiskConfig
from novaarb.shadow_portfolio_cli import _parse_balances, _parse_instruments
from novaarb.shadow_scanner import (
    MultiInstrumentPublicShadowScanner,
    MultiInstrumentShadowEngine,
)
from novaarb.stream_telemetry import StreamTelemetry
from novaarb.unified_shadow import (
    FundingAssetMap,
    UnifiedShadowConfig,
    UnifiedShadowCoordinator,
)
from novaarb.unified_shadow_live import (
    UnifiedShadowLiveConfig,
    run_unified_public_shadow_session,
    unified_metrics,
)
from novaarb.venue import BinancePublicVenueAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "NovaArb unified Binance/Bybit public shadow operation with cross-venue "
            "and funding-carry research; no authenticated orders or transfers"
        )
    )
    parser.add_argument(
        "--instrument",
        action="append",
        required=True,
        metavar="BASE/QUOTE",
        help="repeat for cross-venue Spot instruments, e.g. BTC/USDT",
    )
    parser.add_argument(
        "--funding-instrument",
        action="append",
        required=True,
        metavar="BASE/QUOTE",
        help="repeat for Binance funding research, e.g. BTC/USDT",
    )
    parser.add_argument(
        "--balance",
        action="append",
        required=True,
        metavar="VENUE:ASSET:QUANTITY",
        help="hypothetical pre-funded inventory; repeat for every venue/asset balance",
    )
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--cross-notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--cross-min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=500)
    parser.add_argument("--max-book-skew-ms", type=int, default=150)
    parser.add_argument("--binance-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--bybit-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("1"))
    parser.add_argument("--rebalance-reserve-bps", type=Decimal, default=Decimal("2"))

    parser.add_argument("--funding-notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--funding-intervals", type=int, default=1)
    parser.add_argument("--funding-haircut", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--funding-min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--funding-spot-fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--funding-futures-fee-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--funding-exit-reserve-bps", type=Decimal, default=Decimal("4"))
    parser.add_argument("--funding-basis-risk-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--funding-refresh-seconds", type=int, default=60)
    parser.add_argument("--funding-cooldown-ms", type=int, default=5_000)
    parser.add_argument("--funding-max-age-ms", type=int, default=60_000)
    parser.add_argument(
        "--funding-capital-multiplier",
        type=Decimal,
        default=Decimal("2"),
        help="conservative quote-capital reservation relative to funding reference notional",
    )

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
    parser.add_argument("--execution-delay-ms", type=int, default=50)
    parser.add_argument("--signal-cooldown-ms", type=int, default=1_000)
    parser.add_argument("--max-data-staleness-ms", type=int, default=2_000)
    parser.add_argument("--heartbeat-ms", type=int, default=5_000)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="optional foreground duration; omit to run until interrupted",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="optional public market/evaluation research capture (.jsonl or .jsonl.gz)",
    )
    parser.add_argument(
        "--metrics",
        default=None,
        help="optional heartbeat metrics research log (.jsonl or .jsonl.gz)",
    )
    return parser


def _funding_assets(values: list[str], *, portfolio_quote: str) -> tuple[FundingAssetMap, ...]:
    output: list[FundingAssetMap] = []
    seen: set[str] = set()
    quote = portfolio_quote.upper()
    for raw in values:
        parts = raw.upper().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"invalid funding instrument {raw!r}; expected BASE/QUOTE")
        base, item_quote = parts
        if item_quote != quote:
            raise ValueError("funding instruments must use the unified portfolio quote asset")
        symbol = f"{base}{item_quote}"
        if symbol in seen:
            raise ValueError(f"duplicate funding instrument {base}/{item_quote}")
        seen.add(symbol)
        output.append(FundingAssetMap(symbol, base, item_quote))
    return tuple(output)


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


async def _run(
    args: argparse.Namespace,
) -> tuple[UnifiedShadowCoordinator, StreamTelemetry]:
    instruments = _parse_instruments(args.instrument)
    balances = _parse_balances(args.balance)
    funding_assets = _funding_assets(
        args.funding_instrument,
        portfolio_quote=args.quote,
    )
    costs = _costs(args)
    inventory = MultiAssetInventoryLedger(balances)
    telemetry = StreamTelemetry()
    market_recorder = ResearchRecorder(args.record) if args.record else None
    metrics_recorder = (
        TelemetryResearchRecorder(args.metrics, telemetry=telemetry)
        if args.metrics
        else None
    )

    engine = MultiInstrumentShadowEngine(
        config=CrossVenueConfig(
            target_notional_quote=args.cross_notional,
            min_net_edge_bps=args.cross_min_edge_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        costs=costs,
        inventory=inventory,
        recorder=market_recorder,
    )
    cross_scanner = MultiInstrumentPublicShadowScanner(
        engine=engine,
        adapters=(
            BinancePublicVenueAdapter(telemetry=telemetry),
            BybitPublicVenueAdapter(telemetry=telemetry),
        ),
        instruments=instruments,
        emit_cooldown_ms=args.signal_cooldown_ms,
    )
    funding_config = FundingCarryConfig(
        target_notional_usdt=args.funding_notional,
        funding_intervals=args.funding_intervals,
        funding_haircut=args.funding_haircut,
        spot_taker_fee_bps=args.funding_spot_fee_bps,
        futures_taker_fee_bps=args.funding_futures_fee_bps,
        exit_market_reserve_bps=args.funding_exit_reserve_bps,
        basis_risk_reserve_bps=args.funding_basis_risk_bps,
        min_net_edge_bps=args.funding_min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_book_skew_ms=args.max_book_skew_ms,
        max_funding_age_ms=args.funding_max_age_ms,
    )
    funding_scanner = FundingCarryScanner(
        symbols=tuple(item.symbol for item in funding_assets),
        config=funding_config,
        funding_refresh_seconds=args.funding_refresh_seconds,
        emit_cooldown_ms=args.funding_cooldown_ms,
        recorder=market_recorder,
    )
    coordinator = UnifiedShadowCoordinator(
        engine=engine,
        costs=costs,
        quote_asset=args.quote,
        allocation_config=AllocationConfig(
            total_capital_usdt=args.capital,
            cash_reserve_usdt=args.cash_reserve,
            max_positions=args.max_positions,
            max_capital_per_opportunity_usdt=args.max_capital_per_opportunity,
            min_expected_edge_bps=min(
                args.cross_min_edge_bps,
                args.funding_min_edge_bps,
            ),
        ),
        funding_assets=funding_assets,
        funding_strategy_config=funding_config,
        funding_shadow_config=FundingShadowConfig(
            spot_taker_fee_bps=args.funding_spot_fee_bps,
            futures_taker_fee_bps=args.funding_futures_fee_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        risk_config=ShadowRiskConfig(
            max_daily_loss_quote=args.max_daily_loss,
            max_venue_concentration_pct=args.max_venue_concentration,
            max_asset_concentration_pct=args.max_asset_concentration,
        ),
        config=UnifiedShadowConfig(funding_intervals=args.funding_intervals),
    )
    live_config = UnifiedShadowLiveConfig(
        allocation_window_ms=args.allocation_window_ms,
        execution_delay_ms=args.execution_delay_ms,
        heartbeat_interval_ms=args.heartbeat_ms,
        max_data_staleness_ms=args.max_data_staleness_ms,
        funding_capital_multiplier=args.funding_capital_multiplier,
    )
    await run_unified_public_shadow_session(
        cross_scanner=cross_scanner,
        funding_scanner=funding_scanner,
        coordinator=coordinator,
        metrics_recorder=metrics_recorder,
        duration_seconds=args.duration_seconds,
        config=live_config,
    )
    return coordinator, telemetry


def main() -> None:
    args = _parser().parse_args()
    print(
        "NovaArb unified shadow starting: public market data only; "
        "no authenticated orders or transfers."
    )
    try:
        coordinator, telemetry = asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb unified shadow stopped by operator.")
        return
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    metrics = unified_metrics(
        coordinator,
        max_data_staleness_ms=args.max_data_staleness_ms,
    )
    metrics["stream_telemetry"] = stream_telemetry_payload(telemetry)
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
