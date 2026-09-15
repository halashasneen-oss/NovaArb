from __future__ import annotations

from decimal import Decimal

from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.shadow import (
    AssetBalance,
    MultiAssetInventoryLedger,
    ShadowKillSwitchReason,
    ShadowRiskConfig,
    ShadowRiskGuard,
    VenueWeightTarget,
)


def _ledger() -> MultiAssetInventoryLedger:
    return MultiAssetInventoryLedger(
        (
            AssetBalance("alpha", "USDT", Decimal("1000")),
            AssetBalance("alpha", "BTC", Decimal("0")),
            AssetBalance("alpha", "ETH", Decimal("0")),
            AssetBalance("beta", "USDT", Decimal("100")),
            AssetBalance("beta", "BTC", Decimal("2")),
            AssetBalance("beta", "ETH", Decimal("10")),
        )
    )


def test_multi_asset_ledger_applies_multiple_cross_venue_symbols() -> None:
    ledger = _ledger()
    ledger.apply_cross_venue_trade(
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
    ledger.apply_cross_venue_trade(
        ExecutedCrossVenueTrade(
            buy_venue="alpha",
            sell_venue="beta",
            base_quantity=Decimal("2"),
            buy_quote_spent=Decimal("20"),
            sell_quote_received=Decimal("20.5"),
            buy_fee_quote=Decimal("0.02"),
            sell_fee_quote=Decimal("0.02"),
        ),
        base_asset="ETH",
        quote_asset="USDT",
    )

    assert ledger.balance("alpha", "BTC") == Decimal("1")
    assert ledger.balance("beta", "BTC") == Decimal("1")
    assert ledger.balance("alpha", "ETH") == Decimal("2")
    assert ledger.balance("beta", "ETH") == Decimal("8")
    assert ledger.balance("alpha", "USDT") == Decimal("879.88")
    assert ledger.balance("beta", "USDT") == Decimal("222.38")

    mark = ledger.mark_to_quote(
        quote_asset="USDT",
        prices_in_quote={"BTC": Decimal("101"), "ETH": Decimal("10")},
    )
    assert mark.total_value_quote == Decimal("1404.26")
    assert len(mark.venue_values) == 2
    assert {item.asset for item in mark.asset_values} == {"BTC", "ETH", "USDT"}


def test_shadow_guard_halts_on_unhealthy_data_and_daily_loss() -> None:
    ledger = _ledger()
    mark = ledger.mark_to_quote(
        quote_asset="USDT",
        prices_in_quote={"BTC": Decimal("100"), "ETH": Decimal("10")},
    )
    guard = ShadowRiskGuard(
        ShadowRiskConfig(
            max_daily_loss_quote=Decimal("25"),
            max_venue_concentration_pct=Decimal("0.95"),
            max_asset_concentration_pct=Decimal("0.95"),
        )
    )

    unhealthy = guard.assess(timestamp_ms=1_000, mark=mark, data_healthy=False)
    assert unhealthy.allowed is False
    assert unhealthy.reason is ShadowKillSwitchReason.DATA_UNHEALTHY

    guard.record_realized_pnl(timestamp_ms=1_000, pnl_quote=Decimal("-30"))
    loss = guard.assess(timestamp_ms=1_100, mark=mark, data_healthy=True)
    assert loss.allowed is False
    assert loss.reason is ShadowKillSwitchReason.DAILY_LOSS
    assert loss.daily_pnl_quote == Decimal("-30")


def test_shadow_guard_enforces_venue_weight_drift() -> None:
    ledger = MultiAssetInventoryLedger(
        (
            AssetBalance("alpha", "USDT", Decimal("800")),
            AssetBalance("beta", "USDT", Decimal("200")),
        )
    )
    mark = ledger.mark_to_quote(quote_asset="USDT", prices_in_quote={})
    guard = ShadowRiskGuard(
        ShadowRiskConfig(
            max_daily_loss_quote=Decimal("100"),
            max_venue_concentration_pct=Decimal("0.90"),
            max_asset_concentration_pct=Decimal("1"),
            max_venue_weight_drift_pct=Decimal("0.20"),
            venue_targets=(
                VenueWeightTarget("alpha", Decimal("0.50")),
                VenueWeightTarget("beta", Decimal("0.50")),
            ),
        )
    )

    decision = guard.assess(timestamp_ms=1_000, mark=mark, data_healthy=True)
    assert decision.allowed is False
    assert decision.reason is ShadowKillSwitchReason.VENUE_WEIGHT_DRIFT
    assert decision.max_venue_weight_drift_pct == Decimal("0.30")
