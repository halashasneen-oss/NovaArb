from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.recorder import JsonlRecorder
from novaarb.scanner import ScannerConfig, SpotPerpScanner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NovaArb paper-only spot/perpetual scanner")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    parser.add_argument("--notional", type=Decimal, default=Decimal("50"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=750)
    parser.add_argument("--latency-reserve-bps", type=Decimal, default=Decimal("0.75"))
    parser.add_argument("--record", default=None, help="optional JSONL path for all evaluated events")
    return parser


async def _run(args: argparse.Namespace) -> None:
    recorder = JsonlRecorder(args.record) if args.record else None
    config = ScannerConfig(
        symbols=tuple(symbol.upper() for symbol in args.symbols),
        target_notional_usd=args.notional,
        min_net_edge_bps=args.min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_notional_usd=max(args.notional, Decimal("100")),
        latency_reserve_bps=args.latency_reserve_bps,
    )
    scanner = SpotPerpScanner(config, recorder)
    queue = await scanner.events()
    print("NovaArb scanner running (paper/research mode; no orders can be sent).")
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


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb stopped.")


if __name__ == "__main__":
    main()
