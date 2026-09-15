from __future__ import annotations

import argparse
import json
from decimal import Decimal

from novaarb.allocator import AllocationConfig
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.domain import MarketType
from novaarb.shadow import AssetBalance, ShadowRiskConfig
from novaarb.shadow_metrics import build_shadow_metrics
from novaarb.shadow_portfolio import (
    ShadowPortfolioReplayConfig,
    replay_shadow_portfolio,
)
from novaarb.venue import Instrument, VenueInstrument
from novaarb.venue_health import analyze_venue_health


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NovaArb multi-instrument shadow portfolio replay (research only)"
    )
    parser.add_argument("capture")
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
        help="hypothetical pre-funded inventory; repeat for every venue/asset balance",
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
    parser.add_argument("--allocation-window-ms", type=int, default=100)
    parser.add_argument("--signal-cooldown-ms", type=int, default=1_000)
    parser.add_argument("--max-data-staleness-ms", type=int, default=2_000)
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
    parser.add_argument(
        "--health",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include capture venue-health evidence in JSON output",
    )
    return parser


def _parse_instruments(values: list[str]) -> tuple[VenueInstrument, ...]:
    output: list[VenueInstrument] = []
    seen: set[str] = set()
    for raw in values:
        parts = raw.upper().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"invalid instrument {raw!r}; expected BASE/QUOTE")
        base, quote = parts
        canonical = f"{base}/{quote}"
        if canonical in seen:
            raise ValueError(f"duplicate instrument {canonical}")
        seen.add(canonical)
        instrument = Instrument(base, quote, MarketType.SPOT)
        symbol = f"{base}{quote}"
        output.extend(
            (
                VenueInstrument("binance", symbol, instrument),
                VenueInstrument("bybit", symbol, instrument),
            )
        )
    return tuple(output)


def _parse_balances(values: list[str]) -> tuple[AssetBalance, ...]:
    output: list[AssetBalance] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"invalid balance {raw!r}; expected VENUE:ASSET:QUANTITY"
            )
        venue, asset, quantity = parts
        key = (venue.lower(), asset.upper())
        if key in seen:
            raise ValueError(f"duplicate balance for {key[0]}:{key[1]}")
        seen.add(key)
        output.append(
            AssetBalance(
                venue=key[0],
                asset=key[1],
                quantity=Decimal(quantity),
            )
        )
    return tuple(output)


def main() -> None:
    args = _parser().parse_args()
    try:
        instruments = _parse_instruments(args.instrument)
        balances = _parse_balances(args.balance)
        quote = args.quote.upper()
        costs = (
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
        summary = replay_shadow_portfolio(
            args.capture,
            instruments=instruments,
            costs=costs,
            balances=balances,
            quote_asset=quote,
            allocation_config=AllocationConfig(
                total_capital_usdt=args.capital,
                cash_reserve_usdt=args.cash_reserve,
                max_positions=args.max_positions,
                max_capital_per_opportunity_usdt=args.max_capital_per_opportunity,
                min_expected_edge_bps=args.min_edge_bps,
            ),
            strategy_config=CrossVenueConfig(
                target_notional_quote=args.notional,
                min_net_edge_bps=args.min_edge_bps,
                max_book_age_ms=args.max_book_age_ms,
                max_book_skew_ms=args.max_book_skew_ms,
            ),
            risk_config=ShadowRiskConfig(
                max_daily_loss_quote=args.max_daily_loss,
                max_venue_concentration_pct=args.max_venue_concentration,
                max_asset_concentration_pct=args.max_asset_concentration,
            ),
            replay_config=ShadowPortfolioReplayConfig(
                allocation_window_ms=args.allocation_window_ms,
                signal_cooldown_ms=args.signal_cooldown_ms,
                max_data_staleness_ms=args.max_data_staleness_ms,
            ),
        )
        health = analyze_venue_health(args.capture) if args.health else None
        print(json.dumps(build_shadow_metrics(summary, health=health), indent=2, sort_keys=True))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
