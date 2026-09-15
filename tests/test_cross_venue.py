from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import (
    CrossVenueConfig,
    CrossVenueReason,
    CrossVenueStrategy,
    VenueCostProfile,
    VenueInventory,
)
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.venue import Instrument, NormalizedBook


def _book(
    *,
    venue: str,
    bid: str,
    ask: str,
    received_ms: int = 1_000,
    symbol: str = "BTCUSDT",
    instrument: Instrument | None = None,
) -> NormalizedBook:
    normalized_instrument = instrument or Instrument("BTC", "USDT", MarketType.SPOT)
    snapshot = OrderBookSnapshot(
        venue=venue,
        symbol=symbol,
        market=normalized_instrument.market,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )
    return NormalizedBook(
        instrument=normalized_instrument,
        venue_symbol=symbol,
        snapshot=snapshot,
    )


def _strategy(*, max_age_ms: int = 500, max_skew_ms: int = 150) -> CrossVenueStrategy:
    return CrossVenueStrategy(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("2"),
            max_book_age_ms=max_age_ms,
            max_book_skew_ms=max_skew_ms,
        ),
        costs=(
            VenueCostProfile("alpha", Decimal("1")),
            VenueCostProfile("beta", Decimal("1")),
        ),
    )


def test_best_direction_finds_profitable_prefunded_spread() -> None:
    strategy = _strategy()
    alpha = _book(venue="alpha", bid="99", ask="100")
    beta = _book(venue="beta", bid="102", ask="103")

    result = strategy.best_direction(
        alpha,
        beta,
        now_ms=1_100,
        inventories=(
            VenueInventory("alpha", Decimal("0"), Decimal("200")),
            VenueInventory("beta", Decimal("2"), Decimal("0")),
        ),
    )

    assert result is not None
    opportunity, decision = result
    assert opportunity.buy_venue == "alpha"
    assert opportunity.sell_venue == "beta"
    assert opportunity.net_profit_quote > 0
    assert opportunity.net_edge_bps > Decimal("2")
    assert decision.approved is True
    assert decision.reason is CrossVenueReason.APPROVED


def test_inventory_gate_requires_prefunded_quote_and_base() -> None:
    strategy = _strategy()
    alpha = _book(venue="alpha", bid="99", ask="100")
    beta = _book(venue="beta", bid="102", ask="103")
    opportunity = strategy.evaluate_direction(
        buy_book=alpha,
        sell_book=beta,
        now_ms=1_100,
    )
    assert opportunity is not None

    no_quote = strategy.assess(
        opportunity,
        inventories=(
            VenueInventory("alpha", Decimal("0"), Decimal("1")),
            VenueInventory("beta", Decimal("2"), Decimal("0")),
        ),
    )
    assert no_quote.reason is CrossVenueReason.BUY_QUOTE_INVENTORY

    no_base = strategy.assess(
        opportunity,
        inventories=(
            VenueInventory("alpha", Decimal("0"), Decimal("200")),
            VenueInventory("beta", Decimal("0.1"), Decimal("0")),
        ),
    )
    assert no_base.reason is CrossVenueReason.SELL_BASE_INVENTORY


def test_timing_gates_reject_stale_and_skewed_books() -> None:
    stale_strategy = _strategy(max_age_ms=500)
    alpha = _book(venue="alpha", bid="99", ask="100", received_ms=1_000)
    beta = _book(venue="beta", bid="102", ask="103", received_ms=1_000)
    stale = stale_strategy.evaluate_direction(
        buy_book=alpha,
        sell_book=beta,
        now_ms=2_000,
    )
    assert stale is not None
    assert stale_strategy.assess(stale).reason is CrossVenueReason.STALE_BOOK

    skew_strategy = _strategy(max_age_ms=500, max_skew_ms=150)
    alpha = _book(venue="alpha", bid="99", ask="100", received_ms=1_000)
    beta = _book(venue="beta", bid="102", ask="103", received_ms=1_300)
    skewed = skew_strategy.evaluate_direction(
        buy_book=alpha,
        sell_book=beta,
        now_ms=1_400,
    )
    assert skewed is not None
    assert skew_strategy.assess(skewed).reason is CrossVenueReason.BOOK_SKEW


def test_strategy_refuses_same_venue_and_instrument_mismatch() -> None:
    strategy = _strategy()
    alpha_one = _book(venue="alpha", bid="99", ask="100")
    alpha_two = _book(venue="alpha", bid="102", ask="103")
    assert strategy.best_direction(alpha_one, alpha_two, now_ms=1_100) is None

    eth = Instrument("ETH", "USDT", MarketType.SPOT)
    beta_eth = _book(
        venue="beta",
        bid="102",
        ask="103",
        symbol="ETHUSDT",
        instrument=eth,
    )
    assert strategy.best_direction(alpha_one, beta_eth, now_ms=1_100) is None
