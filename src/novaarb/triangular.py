from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from novaarb.domain import TEN_THOUSAND, MarketType, OrderBookSnapshot, ZERO
from novaarb.symbols import SymbolRules


class ConversionSide(StrEnum):
    BUY_BASE = "buy_base"
    SELL_BASE = "sell_base"


class ConversionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TriangleRoute:
    anchor_asset: str
    first_asset: str
    second_asset: str
    symbols: tuple[str, str, str]

    @property
    def assets(self) -> tuple[str, str, str, str]:
        return (self.anchor_asset, self.first_asset, self.second_asset, self.anchor_asset)

    @property
    def route_id(self) -> str:
        return ">".join(self.assets)


@dataclass(frozen=True, slots=True)
class ConversionFill:
    symbol: str
    from_asset: str
    to_asset: str
    side: ConversionSide
    input_amount: Decimal
    consumed_input: Decimal
    gross_output: Decimal
    fee_paid: Decimal
    net_output: Decimal
    average_price: Decimal
    levels_used: int
    residual_input: Decimal


@dataclass(frozen=True, slots=True)
class TriangularOpportunity:
    strategy: str
    route: TriangleRoute
    starting_amount: Decimal
    final_amount_before_reserve: Decimal
    execution_reserve: Decimal
    net_final_amount: Decimal
    net_profit: Decimal
    net_edge_bps: Decimal
    legs: tuple[ConversionFill, ConversionFill, ConversionFill]
    created_time_ms: int
    max_book_age_ms: int
    book_skew_ms: int


class TriangleRiskReason(StrEnum):
    APPROVED = "approved"
    NON_POSITIVE = "non_positive"
    EDGE_TOO_SMALL = "edge_too_small"
    STALE_BOOK = "stale_book"
    BOOK_SKEW = "book_skew"


@dataclass(frozen=True, slots=True)
class TriangleRiskDecision:
    approved: bool
    reason: TriangleRiskReason


@dataclass(frozen=True, slots=True)
class TriangleConfig:
    starting_amount: Decimal = Decimal("100")
    taker_fee_bps: Decimal = Decimal("10")
    execution_reserve_bps: Decimal = Decimal("2")
    min_net_edge_bps: Decimal = Decimal("2")
    max_book_age_ms: int = 500
    max_book_skew_ms: int = 150


class TrianglePlanner:
    def __init__(self, rules: tuple[SymbolRules, ...]) -> None:
        self.rules = tuple(rule for rule in rules if rule.trading)
        self.by_assets: dict[frozenset[str], SymbolRules] = {}
        for rule in self.rules:
            key = frozenset((rule.base_asset, rule.quote_asset))
            self.by_assets[key] = rule

    def routes(
        self,
        *,
        anchor_asset: str,
        allowed_assets: set[str] | None = None,
    ) -> tuple[TriangleRoute, ...]:
        anchor = anchor_asset.upper()
        assets = {
            asset
            for rule in self.rules
            for asset in (rule.base_asset, rule.quote_asset)
        }
        if allowed_assets is not None:
            assets &= {asset.upper() for asset in allowed_assets}
        assets.discard(anchor)

        output: list[TriangleRoute] = []
        for first in sorted(assets):
            for second in sorted(assets):
                if first == second:
                    continue
                p1 = self.by_assets.get(frozenset((anchor, first)))
                p2 = self.by_assets.get(frozenset((first, second)))
                p3 = self.by_assets.get(frozenset((second, anchor)))
                if p1 is None or p2 is None or p3 is None:
                    continue
                output.append(
                    TriangleRoute(
                        anchor_asset=anchor,
                        first_asset=first,
                        second_asset=second,
                        symbols=(p1.symbol, p2.symbol, p3.symbol),
                    )
                )
        return tuple(output)


def _floor_step(quantity: Decimal, step_size: Decimal) -> Decimal:
    if step_size <= ZERO:
        return quantity
    steps = (quantity / step_size).to_integral_value(rounding=ROUND_DOWN)
    return steps * step_size


class SpotConverter:
    def __init__(self, *, taker_fee_bps: Decimal) -> None:
        if taker_fee_bps < ZERO:
            raise ValueError("fee cannot be negative")
        self.taker_fee_bps = taker_fee_bps

    def convert(
        self,
        *,
        rules: SymbolRules,
        book: OrderBookSnapshot,
        from_asset: str,
        to_asset: str,
        input_amount: Decimal,
    ) -> ConversionFill:
        if book.market is not MarketType.SPOT:
            raise ConversionError("triangular conversion requires spot books")
        if input_amount <= ZERO:
            raise ConversionError("input amount must be positive")
        source = from_asset.upper()
        destination = to_asset.upper()

        if source == rules.quote_asset and destination == rules.base_asset:
            return self._buy_base(rules, book, input_amount)
        if source == rules.base_asset and destination == rules.quote_asset:
            return self._sell_base(rules, book, input_amount)
        raise ConversionError(f"{rules.symbol} cannot convert {source} to {destination}")

    def _buy_base(
        self,
        rules: SymbolRules,
        book: OrderBookSnapshot,
        quote_amount: Decimal,
    ) -> ConversionFill:
        remaining_quote = quote_amount
        gross_base = ZERO
        quote_consumed = ZERO
        levels_used = 0

        for level in book.asks:
            level_quote = level.price * level.quantity
            spend = min(remaining_quote, level_quote)
            if spend > ZERO:
                gross_base += spend / level.price
                quote_consumed += spend
                remaining_quote -= spend
                levels_used += 1
            if remaining_quote == ZERO:
                break

        if remaining_quote > ZERO:
            raise ConversionError(f"insufficient ask depth on {rules.symbol}")
        if quote_consumed < rules.min_notional:
            raise ConversionError(f"below min notional on {rules.symbol}")

        fee = gross_base * self.taker_fee_bps / TEN_THOUSAND
        net_base = gross_base - fee
        average_price = quote_consumed / gross_base
        return ConversionFill(
            symbol=rules.symbol,
            from_asset=rules.quote_asset,
            to_asset=rules.base_asset,
            side=ConversionSide.BUY_BASE,
            input_amount=quote_amount,
            consumed_input=quote_consumed,
            gross_output=gross_base,
            fee_paid=fee,
            net_output=net_base,
            average_price=average_price,
            levels_used=levels_used,
            residual_input=remaining_quote,
        )

    def _sell_base(
        self,
        rules: SymbolRules,
        book: OrderBookSnapshot,
        base_amount: Decimal,
    ) -> ConversionFill:
        executable_base = _floor_step(base_amount, rules.effective_market_step_size)
        if (
            executable_base < rules.effective_market_min_quantity
            or executable_base <= ZERO
        ):
            raise ConversionError(f"below min quantity on {rules.symbol}")

        remaining_base = executable_base
        gross_quote = ZERO
        levels_used = 0
        for level in book.bids:
            take = min(remaining_base, level.quantity)
            if take > ZERO:
                gross_quote += take * level.price
                remaining_base -= take
                levels_used += 1
            if remaining_base == ZERO:
                break

        if remaining_base > ZERO:
            raise ConversionError(f"insufficient bid depth on {rules.symbol}")
        if gross_quote < rules.min_notional:
            raise ConversionError(f"below min notional on {rules.symbol}")

        fee = gross_quote * self.taker_fee_bps / TEN_THOUSAND
        net_quote = gross_quote - fee
        average_price = gross_quote / executable_base
        return ConversionFill(
            symbol=rules.symbol,
            from_asset=rules.base_asset,
            to_asset=rules.quote_asset,
            side=ConversionSide.SELL_BASE,
            input_amount=base_amount,
            consumed_input=executable_base,
            gross_output=gross_quote,
            fee_paid=fee,
            net_output=net_quote,
            average_price=average_price,
            levels_used=levels_used,
            residual_input=base_amount - executable_base,
        )


class TriangularStrategy:
    name = "triangular_spot"

    def __init__(
        self,
        *,
        rules: tuple[SymbolRules, ...],
        config: TriangleConfig,
    ) -> None:
        if config.starting_amount <= ZERO:
            raise ValueError("starting amount must be positive")
        self.rules_by_symbol = {rule.symbol: rule for rule in rules}
        self.config = config
        self.converter = SpotConverter(taker_fee_bps=config.taker_fee_bps)

    def evaluate_route(
        self,
        route: TriangleRoute,
        books: dict[str, OrderBookSnapshot],
        *,
        now_ms: int,
    ) -> TriangularOpportunity | None:
        try:
            route_books = tuple(books[symbol] for symbol in route.symbols)
            route_rules = tuple(self.rules_by_symbol[symbol] for symbol in route.symbols)
        except KeyError:
            return None

        assets = route.assets
        amount = self.config.starting_amount
        legs: list[ConversionFill] = []
        try:
            for index in range(3):
                leg = self.converter.convert(
                    rules=route_rules[index],
                    book=route_books[index],
                    from_asset=assets[index],
                    to_asset=assets[index + 1],
                    input_amount=amount,
                )
                legs.append(leg)
                amount = leg.net_output
        except ConversionError:
            return None

        reserve = (
            self.config.starting_amount
            * self.config.execution_reserve_bps
            / TEN_THOUSAND
        )
        net_final = amount - reserve
        profit = net_final - self.config.starting_amount
        edge_bps = profit / self.config.starting_amount * TEN_THOUSAND
        ages = [book.age_ms(now_ms) for book in route_books]
        max_age = max(ages)
        received_times = [book.received_time_ms for book in route_books]
        book_skew = max(received_times) - min(received_times)
        return TriangularOpportunity(
            strategy=self.name,
            route=route,
            starting_amount=self.config.starting_amount,
            final_amount_before_reserve=amount,
            execution_reserve=reserve,
            net_final_amount=net_final,
            net_profit=profit,
            net_edge_bps=edge_bps,
            legs=(legs[0], legs[1], legs[2]),
            created_time_ms=now_ms,
            max_book_age_ms=max_age,
            book_skew_ms=book_skew,
        )

    def assess(self, opportunity: TriangularOpportunity) -> TriangleRiskDecision:
        if opportunity.net_profit <= ZERO:
            return TriangleRiskDecision(False, TriangleRiskReason.NON_POSITIVE)
        if opportunity.net_edge_bps < self.config.min_net_edge_bps:
            return TriangleRiskDecision(False, TriangleRiskReason.EDGE_TOO_SMALL)
        if opportunity.max_book_age_ms > self.config.max_book_age_ms:
            return TriangleRiskDecision(False, TriangleRiskReason.STALE_BOOK)
        if opportunity.book_skew_ms > self.config.max_book_skew_ms:
            return TriangleRiskDecision(False, TriangleRiskReason.BOOK_SKEW)
        return TriangleRiskDecision(True, TriangleRiskReason.APPROVED)
