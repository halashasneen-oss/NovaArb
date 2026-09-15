from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from novaarb.symbols import fetch_spot_exchange_info
from novaarb.synthetic import SyntheticQuotePlanner, triangle_routes
from novaarb.triangle_scanner import TriangularScanner
from novaarb.triangular import TriangleConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NovaArb executable direct-vs-synthetic quote scanner"
    )
    parser.add_argument("--anchor", default="USDT")
    parser.add_argument("--alternative-quotes", nargs="+", default=["USDC", "FDUSD"])
    parser.add_argument(
        "--base-assets",
        nargs="+",
        default=["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LINK", "LTC"],
    )
    parser.add_argument("--notional", type=Decimal, default=Decimal("100"))
    parser.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--execution-reserve-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--min-edge-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--max-book-age-ms", type=int, default=500)
    parser.add_argument("--max-book-skew-ms", type=int, default=150)
    return parser


async def _run(args: argparse.Namespace) -> None:
    rules = await asyncio.to_thread(fetch_spot_exchange_info)
    planner = SyntheticQuotePlanner(rules)
    planned = planner.routes(
        anchor_quote=args.anchor,
        alternative_quotes=tuple(args.alternative_quotes),
        base_assets=set(args.base_assets),
    )
    if not planned:
        raise SystemExit("no executable synthetic quote routes found")

    routes = triangle_routes(planned)
    used_symbols = {symbol for route in routes for symbol in route.symbols}
    used_rules = tuple(rule for rule in rules if rule.symbol in used_symbols)
    config = TriangleConfig(
        starting_amount=args.notional,
        taker_fee_bps=args.fee_bps,
        execution_reserve_bps=args.execution_reserve_bps,
        min_net_edge_bps=args.min_edge_bps,
        max_book_age_ms=args.max_book_age_ms,
        max_book_skew_ms=args.max_book_skew_ms,
    )
    scanner = TriangularScanner(rules=used_rules, routes=routes, config=config)
    metadata = {route.triangle.route_id: route for route in planned}
    queue = await scanner.events()

    print("NovaArb synthetic quote scanner running (public data; research only).")
    print(f"routes={len(planned)} symbols={len(scanner.symbols)} anchor={args.anchor.upper()}")
    while True:
        event = await queue.get()
        opportunity = event.opportunity
        route = metadata[opportunity.route.route_id]
        print(
            f"{route.base_asset} via {route.alternative_quote} "
            f"direction={route.direction.value} "
            f"net={opportunity.net_profit:.8f} {route.anchor_quote} "
            f"edge={opportunity.net_edge_bps:.3f}bps "
            f"skew={opportunity.book_skew_ms}ms"
        )


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nNovaArb synthetic quote scanner stopped.")


if __name__ == "__main__":
    main()
