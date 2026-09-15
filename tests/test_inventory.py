from __future__ import annotations

from decimal import Decimal

import pytest

from novaarb.cross_venue import VenueInventory
from novaarb.inventory import (
    ExecutedCrossVenueTrade,
    InventoryLedger,
    InventoryRebalancePlanner,
    RebalanceAsset,
    RebalanceConfig,
    VenueInventoryTarget,
)


def test_inventory_ledger_applies_cross_venue_trade() -> None:
    ledger = InventoryLedger(
        (
            VenueInventory("alpha", Decimal("1"), Decimal("200")),
            VenueInventory("beta", Decimal("2"), Decimal("10")),
        )
    )
    balances = ledger.apply(
        ExecutedCrossVenueTrade(
            buy_venue="alpha",
            sell_venue="beta",
            base_quantity=Decimal("0.5"),
            buy_quote_spent=Decimal("50"),
            sell_quote_received=Decimal("52"),
            buy_fee_quote=Decimal("0.05"),
            sell_fee_quote=Decimal("0.05"),
        )
    )

    by_venue = {item.venue: item for item in balances}
    assert by_venue["alpha"].base_available == Decimal("1.5")
    assert by_venue["alpha"].quote_available == Decimal("149.95")
    assert by_venue["beta"].base_available == Decimal("1.5")
    assert by_venue["beta"].quote_available == Decimal("61.95")


def test_inventory_ledger_rejects_unfunded_trade() -> None:
    ledger = InventoryLedger(
        (
            VenueInventory("alpha", Decimal("0"), Decimal("10")),
            VenueInventory("beta", Decimal("0.1"), Decimal("0")),
        )
    )
    trade = ExecutedCrossVenueTrade(
        buy_venue="alpha",
        sell_venue="beta",
        base_quantity=Decimal("0.5"),
        buy_quote_spent=Decimal("50"),
        sell_quote_received=Decimal("52"),
    )
    with pytest.raises(ValueError, match="quote inventory"):
        ledger.apply(trade)


def test_rebalance_plan_restores_target_distribution() -> None:
    planner = InventoryRebalancePlanner(
        targets=(
            VenueInventoryTarget("alpha", Decimal("0.5"), Decimal("0.5")),
            VenueInventoryTarget("beta", Decimal("0.5"), Decimal("0.5")),
        ),
        config=RebalanceConfig(
            base_transfer_cost_bps=Decimal("5"),
            quote_transfer_cost_bps=Decimal("2"),
        ),
    )
    plan = planner.plan(
        (
            VenueInventory("alpha", Decimal("2"), Decimal("0")),
            VenueInventory("beta", Decimal("0"), Decimal("200")),
        ),
        base_reference_price_quote=Decimal("100"),
    )

    assert len(plan.instructions) == 2
    base = next(item for item in plan.instructions if item.asset is RebalanceAsset.BASE)
    quote = next(item for item in plan.instructions if item.asset is RebalanceAsset.QUOTE)
    assert (base.from_venue, base.to_venue, base.amount) == (
        "alpha",
        "beta",
        Decimal("1"),
    )
    assert (quote.from_venue, quote.to_venue, quote.amount) == (
        "beta",
        "alpha",
        Decimal("100"),
    )
    assert base.estimated_cost_quote == Decimal("0.05")
    assert quote.estimated_cost_quote == Decimal("0.02")
    assert plan.total_estimated_cost_quote == Decimal("0.07")


def test_rebalance_minimum_transfer_suppresses_dust() -> None:
    planner = InventoryRebalancePlanner(
        targets=(
            VenueInventoryTarget("alpha", Decimal("0.5"), Decimal("0.5")),
            VenueInventoryTarget("beta", Decimal("0.5"), Decimal("0.5")),
        ),
        config=RebalanceConfig(
            min_base_transfer=Decimal("0.1"),
            min_quote_transfer=Decimal("5"),
        ),
    )
    plan = planner.plan(
        (
            VenueInventory("alpha", Decimal("1.01"), Decimal("101")),
            VenueInventory("beta", Decimal("0.99"), Decimal("99")),
        ),
        base_reference_price_quote=Decimal("100"),
    )
    assert plan.instructions == ()


def test_target_shares_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="base target shares"):
        InventoryRebalancePlanner(
            targets=(
                VenueInventoryTarget("alpha", Decimal("0.6"), Decimal("0.5")),
                VenueInventoryTarget("beta", Decimal("0.5"), Decimal("0.5")),
            )
        )
