from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.bybit import BybitPublicVenueAdapter
from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_scanner import CrossVenuePublicScanner, CrossVenueResearchEngine
from novaarb.domain import MarketType
from novaarb.research import ResearchRecorder
from novaarb.venue import BinancePublicVenueAdapter, Instrument, VenueInstrument


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NovaArb Binance/Bybit public cross-venue research scanner"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
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
    parser.add_argument("--base-per-venue", type=Decimal, default=Decimal("1"))
    parser.add_argument("--quote-per-venue", type=Decimal, default=Decimal("1000"))
    parser.add_argument("--record", default=None)
    return parser


async def _run(args: argparse.Namespace) -> None:
    symbol = args.symbol.upper()
    instrument = Instrument(args.base, args.quote, MarketType.SPOT)
    venues = ("binance", "bybit")
    recorder = ResearchRecorder(args.record) if args.record else None
    engine = CrossVenueResearchEngine(
        config=CrossVenueConfig(
            target_notional_quote=args.notional,
            min_net_edge_bps=args.min_edge_bps,
            max_book_age_ms=args.max_book_age_ms,
            max_book_skew_ms=args.max_book_skew_ms,
        ),
        costs=(
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
        ),
        inventories=tuple(
            VenueInventory(
                venue=venue,
                base_available=args.base_per_venue,
                quote_available=args.quote_per_venue,
            )
            for venue in venues
        ),
        recorder=recorder,
    )
    scanner = CrossVenuePublicScanner(
        engine=engine,
        adapters=(BinancePublicVenueAdapter(), BybitPublicVenueAdapter()),
        instruments=(
            VenueInstrument("binance", symbol, instrument),
            VenueInstrument("bybit", symbol, instrument),
        ),
    )
    queue = await scanner.events()
    print("NovaArb cross-venue scanner running (public data; research only).")
    print(
        f"instrument={instrument.canonical_symbol} symbol={symbol} "
        f"notional={args.notional} quote"
    )
    while True:
        event = await queue.get()
        opportunity = event.opportunity
        print(
            f"buy={opportunity.buy_venue} sell={opportunity.sell_venue} "
            f"edge={opportunity.net_edge_bps:.3f}bps "
            f"expected_net={opportunity.net_profit_quote:.6f} {args.quote.upper()}"
        )


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb cross-venue scanner stopped.")


if __name__ == "__main__":
    main()
