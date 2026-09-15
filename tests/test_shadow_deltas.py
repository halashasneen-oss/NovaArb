from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.shadow import AssetBalance, AssetBalanceDelta, MultiAssetInventoryLedger


def _ledger() -> MultiAssetInventoryLedger:
    return MultiAssetInventoryLedger(
        (
            AssetBalance("binance", "USDT", Decimal("1000")),
            AssetBalance("binance", "BTC", Decimal("1")),
        )
    )


def test_apply_deltas_updates_multiple_assets_atomically() -> None:
    ledger = _ledger()

    ledger.apply_deltas(
        (
            AssetBalanceDelta("binance", "USDT", Decimal("-100")),
            AssetBalanceDelta("binance", "BTC", Decimal("0.5")),
            AssetBalanceDelta("binance", "USDT", Decimal("2")),
        )
    )

    assert ledger.balance("binance", "USDT") == Decimal("902")
    assert ledger.balance("binance", "BTC") == Decimal("1.5")


def test_apply_deltas_rolls_back_when_any_projected_balance_is_negative() -> None:
    ledger = _ledger()

    with pytest.raises(ValueError, match="insufficient inventory"):
        ledger.apply_deltas(
            (
                AssetBalanceDelta("binance", "USDT", Decimal("-50")),
                AssetBalanceDelta("binance", "BTC", Decimal("-2")),
            )
        )

    assert ledger.balance("binance", "USDT") == Decimal("1000")
    assert ledger.balance("binance", "BTC") == Decimal("1")
