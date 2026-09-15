from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from novaarb.symbols import SymbolRules
from novaarb.triangular import TriangleRoute


class SyntheticDirection(StrEnum):
    DIRECT_TO_ALTERNATIVE = "direct_to_alternative"
    ALTERNATIVE_TO_DIRECT = "alternative_to_direct"


@dataclass(frozen=True, slots=True)
class SyntheticQuoteRoute:
    base_asset: str
    anchor_quote: str
    alternative_quote: str
    direction: SyntheticDirection
    triangle: TriangleRoute
    direct_symbol: str
    alternative_symbol: str
    bridge_symbol: str

    @property
    def route_id(self) -> str:
        return (
            f"{self.base_asset}:{self.anchor_quote}/{self.alternative_quote}:"
            f"{self.direction.value}"
        )


class SyntheticQuotePlanner:
    """Builds executable direct-vs-synthetic quote cycles from exchange-listed pairs."""

    def __init__(self, rules: tuple[SymbolRules, ...]) -> None:
        self.rules = tuple(rule for rule in rules if rule.trading)
        self.by_assets: dict[frozenset[str], SymbolRules] = {}
        for rule in self.rules:
            key = frozenset((rule.base_asset, rule.quote_asset))
            existing = self.by_assets.get(key)
            if existing is not None and existing.symbol != rule.symbol:
                raise ValueError(
                    f"ambiguous pair mapping for {sorted(key)}: "
                    f"{existing.symbol} and {rule.symbol}"
                )
            self.by_assets[key] = rule

    def _pair(self, first: str, second: str) -> SymbolRules | None:
        return self.by_assets.get(frozenset((first.upper(), second.upper())))

    def routes(
        self,
        *,
        anchor_quote: str = "USDT",
        alternative_quotes: tuple[str, ...] = ("USDC", "FDUSD"),
        base_assets: set[str] | None = None,
    ) -> tuple[SyntheticQuoteRoute, ...]:
        anchor = anchor_quote.upper()
        alternatives = tuple(
            quote.upper() for quote in alternative_quotes if quote.upper() != anchor
        )
        if not alternatives:
            return ()

        discovered_bases = {
            rule.base_asset
            for rule in self.rules
            if rule.quote_asset == anchor
        }
        if base_assets is not None:
            discovered_bases &= {asset.upper() for asset in base_assets}
        discovered_bases -= {anchor, *alternatives}

        output: list[SyntheticQuoteRoute] = []
        for base in sorted(discovered_bases):
            direct = self._pair(base, anchor)
            if direct is None:
                continue
            for alternative in alternatives:
                alternative_pair = self._pair(base, alternative)
                bridge = self._pair(alternative, anchor)
                if alternative_pair is None or bridge is None:
                    continue

                output.extend(
                    (
                        SyntheticQuoteRoute(
                            base_asset=base,
                            anchor_quote=anchor,
                            alternative_quote=alternative,
                            direction=SyntheticDirection.DIRECT_TO_ALTERNATIVE,
                            triangle=TriangleRoute(
                                anchor_asset=anchor,
                                first_asset=base,
                                second_asset=alternative,
                                symbols=(
                                    direct.symbol,
                                    alternative_pair.symbol,
                                    bridge.symbol,
                                ),
                            ),
                            direct_symbol=direct.symbol,
                            alternative_symbol=alternative_pair.symbol,
                            bridge_symbol=bridge.symbol,
                        ),
                        SyntheticQuoteRoute(
                            base_asset=base,
                            anchor_quote=anchor,
                            alternative_quote=alternative,
                            direction=SyntheticDirection.ALTERNATIVE_TO_DIRECT,
                            triangle=TriangleRoute(
                                anchor_asset=anchor,
                                first_asset=alternative,
                                second_asset=base,
                                symbols=(
                                    bridge.symbol,
                                    alternative_pair.symbol,
                                    direct.symbol,
                                ),
                            ),
                            direct_symbol=direct.symbol,
                            alternative_symbol=alternative_pair.symbol,
                            bridge_symbol=bridge.symbol,
                        ),
                    )
                )
        return tuple(output)


def triangle_routes(routes: tuple[SyntheticQuoteRoute, ...]) -> tuple[TriangleRoute, ...]:
    return tuple(route.triangle for route in routes)
