from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.symbols import SymbolRules
from novaarb.synthetic import SyntheticDirection, SyntheticQuotePlanner, triangle_routes


def _rule(symbol: str, base: str, quote: str) -> SymbolRules:
    return SymbolRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        status="TRADING",
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.000001"),
        min_notional=Decimal("1"),
    )


def test_planner_builds_both_direct_and_reverse_quote_cycles() -> None:
    rules = (
        _rule("BTCUSDT", "BTC", "USDT"),
        _rule("BTCUSDC", "BTC", "USDC"),
        _rule("USDCUSDT", "USDC", "USDT"),
        _rule("ETHUSDT", "ETH", "USDT"),
    )
    planner = SyntheticQuotePlanner(rules)
    routes = planner.routes(
        anchor_quote="USDT",
        alternative_quotes=("USDC", "FDUSD"),
        base_assets={"BTC", "ETH"},
    )

    assert len(routes) == 2
    assert {route.direction for route in routes} == {
        SyntheticDirection.DIRECT_TO_ALTERNATIVE,
        SyntheticDirection.ALTERNATIVE_TO_DIRECT,
    }
    direct = next(
        route for route in routes if route.direction is SyntheticDirection.DIRECT_TO_ALTERNATIVE
    )
    reverse = next(
        route for route in routes if route.direction is SyntheticDirection.ALTERNATIVE_TO_DIRECT
    )
    assert direct.triangle.assets == ("USDT", "BTC", "USDC", "USDT")
    assert reverse.triangle.assets == ("USDT", "USDC", "BTC", "USDT")
    assert direct.direct_symbol == "BTCUSDT"
    assert direct.alternative_symbol == "BTCUSDC"
    assert direct.bridge_symbol == "USDCUSDT"
    assert triangle_routes(routes) == tuple(route.triangle for route in routes)


def test_planner_ignores_incomplete_synthetic_path() -> None:
    planner = SyntheticQuotePlanner(
        (
            _rule("BTCUSDT", "BTC", "USDT"),
            _rule("BTCUSDC", "BTC", "USDC"),
        )
    )
    assert planner.routes(anchor_quote="USDT", alternative_quotes=("USDC",)) == ()


def test_planner_rejects_ambiguous_asset_pair_mapping() -> None:
    with pytest.raises(ValueError, match="ambiguous pair mapping"):
        SyntheticQuotePlanner(
            (
                _rule("USDCUSDT", "USDC", "USDT"),
                _rule("USDTUSDC", "USDT", "USDC"),
            )
        )
