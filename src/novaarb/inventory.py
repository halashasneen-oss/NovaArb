from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.cross_venue import VenueInventory
from novaarb.domain import TEN_THOUSAND, ZERO


@dataclass(frozen=True, slots=True)
class ExecutedCrossVenueTrade:
    buy_venue: str
    sell_venue: str
    base_quantity: Decimal
    buy_quote_spent: Decimal
    sell_quote_received: Decimal
    buy_fee_quote: Decimal = ZERO
    sell_fee_quote: Decimal = ZERO

    def __post_init__(self) -> None:
        if not self.buy_venue or not self.sell_venue:
            raise ValueError("buy_venue and sell_venue are required")
        if self.buy_venue == self.sell_venue:
            raise ValueError("cross-venue trade requires distinct venues")
        if min(
            self.base_quantity,
            self.buy_quote_spent,
            self.sell_quote_received,
        ) <= ZERO:
            raise ValueError("trade quantities must be positive")
        if self.buy_fee_quote < ZERO or self.sell_fee_quote < ZERO:
            raise ValueError("trade fees cannot be negative")


class InventoryLedger:
    """In-memory inventory accounting for pre-funded cross-venue research."""

    def __init__(self, inventories: tuple[VenueInventory, ...]) -> None:
        if not inventories:
            raise ValueError("at least one venue inventory is required")
        if len({item.venue for item in inventories}) != len(inventories):
            raise ValueError("venue inventories must be unique")
        self._balances = {item.venue: item for item in inventories}

    def snapshot(self) -> tuple[VenueInventory, ...]:
        return tuple(self._balances[venue] for venue in sorted(self._balances))

    def apply(self, trade: ExecutedCrossVenueTrade) -> tuple[VenueInventory, ...]:
        buy = self._balances.get(trade.buy_venue)
        sell = self._balances.get(trade.sell_venue)
        if buy is None or sell is None:
            raise ValueError("trade references an unknown venue")

        buy_quote_required = trade.buy_quote_spent + trade.buy_fee_quote
        if buy.quote_available < buy_quote_required:
            raise ValueError("insufficient quote inventory on buy venue")
        if sell.base_available < trade.base_quantity:
            raise ValueError("insufficient base inventory on sell venue")
        if trade.sell_fee_quote > trade.sell_quote_received:
            raise ValueError("sell fee exceeds quote proceeds")

        self._balances[trade.buy_venue] = VenueInventory(
            venue=buy.venue,
            base_available=buy.base_available + trade.base_quantity,
            quote_available=buy.quote_available - buy_quote_required,
        )
        self._balances[trade.sell_venue] = VenueInventory(
            venue=sell.venue,
            base_available=sell.base_available - trade.base_quantity,
            quote_available=(
                sell.quote_available
                + trade.sell_quote_received
                - trade.sell_fee_quote
            ),
        )
        return self.snapshot()


@dataclass(frozen=True, slots=True)
class VenueInventoryTarget:
    venue: str
    base_share: Decimal
    quote_share: Decimal

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue is required")
        if not ZERO <= self.base_share <= Decimal("1"):
            raise ValueError("base_share must be between 0 and 1")
        if not ZERO <= self.quote_share <= Decimal("1"):
            raise ValueError("quote_share must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RebalanceConfig:
    base_transfer_cost_bps: Decimal = Decimal("0")
    quote_transfer_cost_bps: Decimal = Decimal("0")
    min_base_transfer: Decimal = Decimal("0")
    min_quote_transfer: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if min(
            self.base_transfer_cost_bps,
            self.quote_transfer_cost_bps,
            self.min_base_transfer,
            self.min_quote_transfer,
        ) < ZERO:
            raise ValueError("rebalance configuration cannot be negative")


class RebalanceAsset(StrEnum):
    BASE = "base"
    QUOTE = "quote"


@dataclass(frozen=True, slots=True)
class RebalanceInstruction:
    asset: RebalanceAsset
    from_venue: str
    to_venue: str
    amount: Decimal
    estimated_cost_quote: Decimal


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    instructions: tuple[RebalanceInstruction, ...]
    total_estimated_cost_quote: Decimal
    total_base_moved: Decimal
    total_quote_moved: Decimal


@dataclass(slots=True)
class _TransferBalance:
    venue: str
    amount: Decimal


class InventoryRebalancePlanner:
    """Builds research-only inventory transfer plans; it never moves funds."""

    def __init__(
        self,
        *,
        targets: tuple[VenueInventoryTarget, ...],
        config: RebalanceConfig | None = None,
    ) -> None:
        if not targets:
            raise ValueError("at least one target is required")
        if len({target.venue for target in targets}) != len(targets):
            raise ValueError("target venues must be unique")
        if sum((target.base_share for target in targets), ZERO) != Decimal("1"):
            raise ValueError("base target shares must sum to 1")
        if sum((target.quote_share for target in targets), ZERO) != Decimal("1"):
            raise ValueError("quote target shares must sum to 1")
        self.targets = targets
        self.config = config if config is not None else RebalanceConfig()

    def plan(
        self,
        inventories: tuple[VenueInventory, ...],
        *,
        base_reference_price_quote: Decimal,
    ) -> RebalancePlan:
        if base_reference_price_quote <= ZERO:
            raise ValueError("base_reference_price_quote must be positive")
        by_venue = {item.venue: item for item in inventories}
        target_venues = {target.venue for target in self.targets}
        if set(by_venue) != target_venues:
            raise ValueError("inventory venues must exactly match target venues")

        total_base = sum((item.base_available for item in inventories), ZERO)
        total_quote = sum((item.quote_available for item in inventories), ZERO)
        instructions = [
            *self._plan_asset(
                inventories=by_venue,
                total=total_base,
                asset=RebalanceAsset.BASE,
                min_transfer=self.config.min_base_transfer,
                cost_bps=self.config.base_transfer_cost_bps,
                quote_value_per_unit=base_reference_price_quote,
            ),
            *self._plan_asset(
                inventories=by_venue,
                total=total_quote,
                asset=RebalanceAsset.QUOTE,
                min_transfer=self.config.min_quote_transfer,
                cost_bps=self.config.quote_transfer_cost_bps,
                quote_value_per_unit=Decimal("1"),
            ),
        ]
        total_cost = sum((item.estimated_cost_quote for item in instructions), ZERO)
        return RebalancePlan(
            instructions=tuple(instructions),
            total_estimated_cost_quote=total_cost,
            total_base_moved=sum(
                (
                    item.amount
                    for item in instructions
                    if item.asset is RebalanceAsset.BASE
                ),
                ZERO,
            ),
            total_quote_moved=sum(
                (
                    item.amount
                    for item in instructions
                    if item.asset is RebalanceAsset.QUOTE
                ),
                ZERO,
            ),
        )

    def _plan_asset(
        self,
        *,
        inventories: dict[str, VenueInventory],
        total: Decimal,
        asset: RebalanceAsset,
        min_transfer: Decimal,
        cost_bps: Decimal,
        quote_value_per_unit: Decimal,
    ) -> list[RebalanceInstruction]:
        donors: list[_TransferBalance] = []
        receivers: list[_TransferBalance] = []
        target_map = {target.venue: target for target in self.targets}

        for venue in sorted(inventories):
            inventory = inventories[venue]
            target = target_map[venue]
            current = (
                inventory.base_available
                if asset is RebalanceAsset.BASE
                else inventory.quote_available
            )
            share = (
                target.base_share
                if asset is RebalanceAsset.BASE
                else target.quote_share
            )
            delta = current - total * share
            if delta > ZERO:
                donors.append(_TransferBalance(venue, delta))
            elif delta < ZERO:
                receivers.append(_TransferBalance(venue, -delta))

        output: list[RebalanceInstruction] = []
        donor_index = 0
        receiver_index = 0
        while donor_index < len(donors) and receiver_index < len(receivers):
            donor = donors[donor_index]
            receiver = receivers[receiver_index]
            amount = min(donor.amount, receiver.amount)

            if amount >= min_transfer and amount > ZERO:
                estimated_cost = (
                    amount * quote_value_per_unit * cost_bps / TEN_THOUSAND
                )
                output.append(
                    RebalanceInstruction(
                        asset=asset,
                        from_venue=donor.venue,
                        to_venue=receiver.venue,
                        amount=amount,
                        estimated_cost_quote=estimated_cost,
                    )
                )

            donor.amount -= amount
            receiver.amount -= amount
            if donor.amount == ZERO:
                donor_index += 1
            if receiver.amount == ZERO:
                receiver_index += 1

        return output
