from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from novaarb.allocator import AllocationConfig, AllocationReason
from novaarb.candidate_bus import ShadowCandidateBus, funding_envelope
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.funding import FundingCarryOpportunity, FundingSnapshot
from novaarb.funding_shadow import FundingShadowBook, FundingShadowConfig
from novaarb.shadow import AssetBalance, MultiAssetInventoryLedger


def _opportunity() -> FundingCarryOpportunity:
    return FundingCarryOpportunity(
        symbol="BTCUSDT",
        strategy="funding_carry_long_spot_short_perp",
        base_quantity=Decimal("1"),
        spot_entry_price=Decimal("100"),
        futures_entry_price=Decimal("101"),
        reference_notional_usdt=Decimal("100"),
        funding_rate_bps=Decimal("50"),
        current_basis_bps=Decimal("100"),
        expected_funding_usdt=Decimal("0.5"),
        entry_execution_cost_usdt=Decimal("0"),
        entry_fees_usdt=Decimal("0.02"),
        exit_reserve_usdt=Decimal("0.02"),
        basis_risk_reserve_usdt=Decimal("0.02"),
        expected_net_profit_usdt=Decimal("0.44"),
        expected_net_edge_bps=Decimal("44"),
        created_time_ms=1_000,
        spot_book_age_ms=0,
        futures_book_age_ms=0,
        funding_age_ms=0,
    )


def _funding(*, received_ms: int = 900, target_ms: int = 5_000) -> FundingSnapshot:
    return FundingSnapshot(
        symbol="BTCUSDT",
        funding_rate=Decimal("0.005"),
        next_funding_time_ms=target_ms,
        mark_price=Decimal("101"),
        index_price=Decimal("100.9"),
        received_time_ms=received_ms,
    )


def _book(
    *,
    market: MarketType,
    bid: str,
    ask: str,
    received_ms: int,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue="binance",
        symbol="BTCUSDT",
        market=market,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _inventory() -> MultiAssetInventoryLedger:
    return MultiAssetInventoryLedger(
        (
            AssetBalance("binance", "USDT", Decimal("1000")),
            AssetBalance("binance", "BTC", Decimal("0")),
        )
    )


def test_funding_shadow_reserves_capital_settles_and_releases_realized_pnl() -> None:
    inventory = _inventory()
    book = FundingShadowBook(
        inventory=inventory,
        config=FundingShadowConfig(
            spot_taker_fee_bps=Decimal("1"),
            futures_taker_fee_bps=Decimal("1"),
            max_book_age_ms=100,
            max_book_skew_ms=20,
        ),
    )
    book.observe_funding(_funding())
    envelope = funding_envelope(
        _opportunity(),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )

    position = book.open_from_envelope(
        envelope,
        base_asset="BTC",
        quote_asset="USDT",
        funding_intervals=1,
    )

    assert position.next_funding_time_ms == 5_000
    assert book.reserved_capital_quote == Decimal("200")
    assert inventory.balance("binance", "USDT") == Decimal("800")
    settlements = book.settle_due(now_ms=5_000)
    assert len(settlements) == 1
    assert settlements[0].payment_quote == Decimal("0.505")
    assert settlements[0].ready_to_close is True

    close = book.close_ready(
        position.position_id,
        spot_book=_book(
            market=MarketType.SPOT,
            bid="101.0",
            ask="101.1",
            received_ms=5_010,
        ),
        perpetual_book=_book(
            market=MarketType.PERPETUAL,
            bid="101.1",
            ask="101.2",
            received_ms=5_011,
        ),
        now_ms=5_011,
    )

    assert close.funding_received_quote == Decimal("0.505")
    assert close.realized_net_profit_quote > 0
    assert close.realized_edge_bps > 0
    assert book.open_positions == 0
    assert book.reserved_capital_quote == 0
    assert (
        inventory.balance("binance", "USDT")
        == Decimal("1000") + close.realized_net_profit_quote
    )


def test_open_funding_position_exposes_allocator_locks_for_new_candidates() -> None:
    inventory = _inventory()
    positions = FundingShadowBook(inventory=inventory)
    positions.observe_funding(_funding())
    funding = funding_envelope(
        _opportunity(),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )
    positions.open_from_envelope(
        funding,
        base_asset="BTC",
        quote_asset="USDT",
        funding_intervals=1,
    )

    competing = funding_envelope(
        replace(
            _opportunity(),
            expected_net_profit_usdt=Decimal("1"),
            expected_net_edge_bps=Decimal("60"),
            created_time_ms=1_100,
        ),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=2,
    )
    bus = ShadowCandidateBus(
        AllocationConfig(
            total_capital_usdt=Decimal("1000"),
            max_positions=4,
            max_capital_per_opportunity_usdt=Decimal("300"),
            min_expected_edge_bps=Decimal("1"),
        )
    )
    result = bus.allocate(
        (competing,),
        reserved_capital_usdt=positions.reserved_capital_quote,
        open_positions=positions.open_positions,
        used_resources=positions.used_resources,
        strategy_capital_usdt=positions.strategy_capital(),
        strategy_positions=positions.strategy_positions(),
    )

    assert result.selected == ()
    assert result.decisions[0].reason is AllocationReason.RESOURCE_CONFLICT


def test_funding_shadow_mark_includes_hedged_unrealized_move() -> None:
    inventory = _inventory()
    book = FundingShadowBook(inventory=inventory)
    book.observe_funding(_funding())
    envelope = funding_envelope(
        _opportunity(),
        base_asset="BTC",
        quote_asset="USDT",
        sequence=1,
    )
    position = book.open_from_envelope(
        envelope,
        base_asset="BTC",
        quote_asset="USDT",
        funding_intervals=1,
    )

    marked = book.mark_position_quote(
        position.position_id,
        spot_mid_price=Decimal("105"),
        futures_mid_price=Decimal("106"),
    )

    assert marked == Decimal("199.98")
