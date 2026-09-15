from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import ZERO
from novaarb.inventory import ExecutedCrossVenueTrade


@dataclass(frozen=True, slots=True)
class AssetBalance:
    venue: str
    asset: str
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.venue or not self.asset:
            raise ValueError("venue and asset are required")
        if self.quantity < ZERO:
            raise ValueError("asset quantity cannot be negative")


@dataclass(frozen=True, slots=True)
class VenueMark:
    venue: str
    value_quote: Decimal


@dataclass(frozen=True, slots=True)
class AssetMark:
    asset: str
    value_quote: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioMark:
    quote_asset: str
    total_value_quote: Decimal
    venue_values: tuple[VenueMark, ...]
    asset_values: tuple[AssetMark, ...]


class MultiAssetInventoryLedger:
    """Pre-funded venue/asset accounting shared by multiple cross-venue symbols."""

    def __init__(self, balances: tuple[AssetBalance, ...]) -> None:
        if not balances:
            raise ValueError("at least one asset balance is required")
        keys = [(item.venue, item.asset) for item in balances]
        if len(keys) != len(set(keys)):
            raise ValueError("venue/asset balances must be unique")
        self._balances = {(item.venue, item.asset): item.quantity for item in balances}

    def balance(self, venue: str, asset: str) -> Decimal:
        return self._balances.get((venue, asset), ZERO)

    def snapshot(self) -> tuple[AssetBalance, ...]:
        return tuple(
            AssetBalance(venue=venue, asset=asset, quantity=quantity)
            for (venue, asset), quantity in sorted(self._balances.items())
        )

    def apply_cross_venue_trade(
        self,
        trade: ExecutedCrossVenueTrade,
        *,
        base_asset: str,
        quote_asset: str,
    ) -> tuple[AssetBalance, ...]:
        if not base_asset or not quote_asset or base_asset == quote_asset:
            raise ValueError("distinct base_asset and quote_asset are required")

        buy_quote_key = (trade.buy_venue, quote_asset)
        buy_base_key = (trade.buy_venue, base_asset)
        sell_base_key = (trade.sell_venue, base_asset)
        sell_quote_key = (trade.sell_venue, quote_asset)

        buy_quote_required = trade.buy_quote_spent + trade.buy_fee_quote
        sell_quote_net = trade.sell_quote_received - trade.sell_fee_quote
        if sell_quote_net < ZERO:
            raise ValueError("sell fee exceeds quote proceeds")
        if self._balances.get(buy_quote_key, ZERO) < buy_quote_required:
            raise ValueError("insufficient quote inventory on buy venue")
        if self._balances.get(sell_base_key, ZERO) < trade.base_quantity:
            raise ValueError("insufficient base inventory on sell venue")

        self._balances[buy_quote_key] = (
            self._balances.get(buy_quote_key, ZERO) - buy_quote_required
        )
        self._balances[buy_base_key] = (
            self._balances.get(buy_base_key, ZERO) + trade.base_quantity
        )
        self._balances[sell_base_key] = (
            self._balances.get(sell_base_key, ZERO) - trade.base_quantity
        )
        self._balances[sell_quote_key] = (
            self._balances.get(sell_quote_key, ZERO) + sell_quote_net
        )
        return self.snapshot()

    def mark_to_quote(
        self,
        *,
        quote_asset: str,
        prices_in_quote: dict[str, Decimal],
    ) -> PortfolioMark:
        if not quote_asset:
            raise ValueError("quote_asset is required")
        venue_values: dict[str, Decimal] = {}
        asset_values: dict[str, Decimal] = {}

        for (venue, asset), quantity in self._balances.items():
            if quantity == ZERO:
                continue
            if asset == quote_asset:
                price = Decimal("1")
            else:
                price = prices_in_quote.get(asset, ZERO)
                if price <= ZERO:
                    raise ValueError(f"missing positive mark price for {asset}")
            value = quantity * price
            venue_values[venue] = venue_values.get(venue, ZERO) + value
            asset_values[asset] = asset_values.get(asset, ZERO) + value

        total = sum(venue_values.values(), ZERO)
        return PortfolioMark(
            quote_asset=quote_asset,
            total_value_quote=total,
            venue_values=tuple(
                VenueMark(venue=venue, value_quote=value)
                for venue, value in sorted(venue_values.items())
            ),
            asset_values=tuple(
                AssetMark(asset=asset, value_quote=value)
                for asset, value in sorted(asset_values.items())
            ),
        )


@dataclass(frozen=True, slots=True)
class VenueWeightTarget:
    venue: str
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue is required")
        if self.weight < ZERO or self.weight > Decimal("1"):
            raise ValueError("venue target weight must be between 0 and 1")


class ShadowKillSwitchReason(StrEnum):
    NONE = "none"
    DATA_UNHEALTHY = "data_unhealthy"
    DAILY_LOSS = "daily_loss"
    VENUE_CONCENTRATION = "venue_concentration"
    VENUE_WEIGHT_DRIFT = "venue_weight_drift"
    ASSET_CONCENTRATION = "asset_concentration"


@dataclass(frozen=True, slots=True)
class ShadowRiskConfig:
    max_daily_loss_quote: Decimal = Decimal("25")
    max_venue_concentration_pct: Decimal = Decimal("0.70")
    max_asset_concentration_pct: Decimal = Decimal("0.80")
    max_venue_weight_drift_pct: Decimal = Decimal("0.15")
    halt_on_unhealthy_data: bool = True
    venue_targets: tuple[VenueWeightTarget, ...] = ()

    def __post_init__(self) -> None:
        if self.max_daily_loss_quote <= ZERO:
            raise ValueError("max_daily_loss_quote must be positive")
        percentages = (
            self.max_venue_concentration_pct,
            self.max_asset_concentration_pct,
            self.max_venue_weight_drift_pct,
        )
        if any(value < ZERO or value > Decimal("1") for value in percentages):
            raise ValueError("risk percentages must be between 0 and 1")
        if self.venue_targets:
            names = [item.venue for item in self.venue_targets]
            if len(names) != len(set(names)):
                raise ValueError("venue target names must be unique")
            total = sum((item.weight for item in self.venue_targets), ZERO)
            if total != Decimal("1"):
                raise ValueError("venue target weights must sum to 1")


@dataclass(frozen=True, slots=True)
class ShadowRiskDecision:
    allowed: bool
    reason: ShadowKillSwitchReason
    total_value_quote: Decimal
    daily_pnl_quote: Decimal
    max_venue_concentration_pct: Decimal
    max_asset_concentration_pct: Decimal
    max_venue_weight_drift_pct: Decimal


class ShadowRiskGuard:
    """Research-only portfolio guard; it never submits or cancels exchange orders."""

    def __init__(self, config: ShadowRiskConfig | None = None) -> None:
        self.config = config or ShadowRiskConfig()
        self._daily_pnl: dict[int, Decimal] = {}

    def record_realized_pnl(self, *, timestamp_ms: int, pnl_quote: Decimal) -> Decimal:
        day = timestamp_ms // 86_400_000
        updated = self._daily_pnl.get(day, ZERO) + pnl_quote
        self._daily_pnl[day] = updated
        return updated

    def daily_pnl(self, timestamp_ms: int) -> Decimal:
        return self._daily_pnl.get(timestamp_ms // 86_400_000, ZERO)

    def assess(
        self,
        *,
        timestamp_ms: int,
        mark: PortfolioMark,
        data_healthy: bool,
    ) -> ShadowRiskDecision:
        if mark.total_value_quote <= ZERO:
            raise ValueError("portfolio mark must have positive total value")

        venue_concentration = max(
            (item.value_quote / mark.total_value_quote for item in mark.venue_values),
            default=ZERO,
        )
        asset_concentration = max(
            (item.value_quote / mark.total_value_quote for item in mark.asset_values),
            default=ZERO,
        )
        target_map = {item.venue: item.weight for item in self.config.venue_targets}
        actual_map = {
            item.venue: item.value_quote / mark.total_value_quote
            for item in mark.venue_values
        }
        all_venues = set(target_map) | set(actual_map)
        max_drift = max(
            (
                abs(actual_map.get(venue, ZERO) - target_map.get(venue, ZERO))
                for venue in all_venues
            ),
            default=ZERO,
        ) if target_map else ZERO
        daily_pnl = self.daily_pnl(timestamp_ms)

        reason = ShadowKillSwitchReason.NONE
        if self.config.halt_on_unhealthy_data and not data_healthy:
            reason = ShadowKillSwitchReason.DATA_UNHEALTHY
        elif daily_pnl <= -self.config.max_daily_loss_quote:
            reason = ShadowKillSwitchReason.DAILY_LOSS
        elif venue_concentration > self.config.max_venue_concentration_pct:
            reason = ShadowKillSwitchReason.VENUE_CONCENTRATION
        elif target_map and max_drift > self.config.max_venue_weight_drift_pct:
            reason = ShadowKillSwitchReason.VENUE_WEIGHT_DRIFT
        elif asset_concentration > self.config.max_asset_concentration_pct:
            reason = ShadowKillSwitchReason.ASSET_CONCENTRATION

        return ShadowRiskDecision(
            allowed=reason is ShadowKillSwitchReason.NONE,
            reason=reason,
            total_value_quote=mark.total_value_quote,
            daily_pnl_quote=daily_pnl,
            max_venue_concentration_pct=venue_concentration,
            max_asset_concentration_pct=asset_concentration,
            max_venue_weight_drift_pct=max_drift,
        )
