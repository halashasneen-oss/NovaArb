from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.shadow import AssetBalance, MultiAssetInventoryLedger
from novaarb.shadow_scanner import MultiInstrumentShadowEngine
from novaarb.venue import Instrument, NormalizedBook


def _book(
    *,
    venue: str,
    base: str,
    bid: str,
    ask: str,
    received_ms: int,
) -> NormalizedBook:
    instrument = Instrument(base, "USDT", MarketType.SPOT)
    symbol = f"{base}USDT"
    return NormalizedBook(
        instrument=instrument,
        venue_symbol=symbol,
        snapshot=OrderBookSnapshot(
            venue=venue,
            symbol=symbol,
            market=MarketType.SPOT,
            bids=(BookLevel(Decimal(bid), Decimal("100")),),
            asks=(BookLevel(Decimal(ask), Decimal("100")),),
            event_time_ms=received_ms,
            received_time_ms=received_ms,
        ),
    )


def _engine() -> MultiInstrumentShadowEngine:
    ledger = MultiAssetInventoryLedger(
        (
            AssetBalance("alpha", "USDT", Decimal("1000")),
            AssetBalance("alpha", "BTC", Decimal("0")),
            AssetBalance("alpha", "ETH", Decimal("0")),
            AssetBalance("beta", "USDT", Decimal("100")),
            AssetBalance("beta", "BTC", Decimal("2")),
            AssetBalance("beta", "ETH", Decimal("20")),
        )
    )
    return MultiInstrumentShadowEngine(
        config=CrossVenueConfig(
            target_notional_quote=Decimal("100"),
            min_net_edge_bps=Decimal("1"),
        ),
        costs=(
            VenueCostProfile("alpha", Decimal("1")),
            VenueCostProfile("beta", Decimal("1")),
        ),
        inventory=ledger,
    )


def test_shadow_engine_scans_multiple_symbols_against_shared_inventory() -> None:
    engine = _engine()

    assert engine.process_book(
        _book(venue="alpha", base="BTC", bid="99", ask="100", received_ms=1_000)
    ) == ()
    btc_events = engine.process_book(
        _book(venue="beta", base="BTC", bid="102", ask="103", received_ms=1_000)
    )
    assert len(btc_events) == 1
    assert btc_events[0].decision.approved is True
    assert btc_events[0].opportunity.instrument.base_asset == "BTC"

    assert engine.process_book(
        _book(venue="alpha", base="ETH", bid="9.9", ask="10", received_ms=2_000)
    ) == ()
    eth_events = engine.process_book(
        _book(venue="beta", base="ETH", bid="10.2", ask="10.3", received_ms=2_000)
    )
    assert len(eth_events) == 1
    assert eth_events[0].decision.approved is True
    assert eth_events[0].opportunity.instrument.base_asset == "ETH"


def test_shadow_fill_updates_only_the_executed_asset_inventory() -> None:
    engine = _engine()
    engine.apply_shadow_fill(
        ExecutedCrossVenueTrade(
            buy_venue="alpha",
            sell_venue="beta",
            base_quantity=Decimal("1"),
            buy_quote_spent=Decimal("100"),
            sell_quote_received=Decimal("102"),
            buy_fee_quote=Decimal("0.1"),
            sell_fee_quote=Decimal("0.1"),
        ),
        base_asset="BTC",
        quote_asset="USDT",
    )

    assert engine.inventory.balance("alpha", "BTC") == Decimal("1")
    assert engine.inventory.balance("beta", "BTC") == Decimal("1")
    assert engine.inventory.balance("alpha", "ETH") == Decimal("0")
    assert engine.inventory.balance("beta", "ETH") == Decimal("20")
