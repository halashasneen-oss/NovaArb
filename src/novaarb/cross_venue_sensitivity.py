from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile, VenueInventory
from novaarb.cross_venue_replay import (
    CrossVenueLatencyProfile,
    CrossVenueReplayConfig,
    replay_cross_venue_capture,
)
from novaarb.domain import ZERO
from novaarb.inventory import RebalanceConfig


@dataclass(frozen=True, slots=True)
class CrossVenueSensitivityPoint:
    taker_fee_bps: Decimal
    transfer_cost_bps: Decimal
    detected_signals: int
    completed_trades: int
    profitable_trades: int
    total_net_profit_quote: Decimal
    rebalance_cost_quote: Decimal
    net_after_rebalance_quote: Decimal
    median_realized_edge_bps: Decimal
    median_edge_decay_bps: Decimal


@dataclass(frozen=True, slots=True)
class CrossVenueSensitivityReport:
    fee_tiers_bps: tuple[Decimal, ...]
    transfer_costs_bps: tuple[Decimal, ...]
    points: tuple[CrossVenueSensitivityPoint, ...]


def run_cross_venue_sensitivity_matrix(
    path: str,
    *,
    base_asset: str,
    quote_asset: str,
    base_costs: tuple[VenueCostProfile, ...],
    inventories: tuple[VenueInventory, ...],
    fee_tiers_bps: tuple[Decimal, ...],
    transfer_costs_bps: tuple[Decimal, ...],
    strategy_config: CrossVenueConfig | None = None,
    latency: CrossVenueLatencyProfile | None = None,
    replay: CrossVenueReplayConfig | None = None,
) -> CrossVenueSensitivityReport:
    """Replay one capture across taker-fee and inventory-transfer assumptions."""

    if not fee_tiers_bps or not transfer_costs_bps:
        raise ValueError("fee and transfer sensitivity grids must be non-empty")
    if any(value < ZERO for value in (*fee_tiers_bps, *transfer_costs_bps)):
        raise ValueError("sensitivity assumptions cannot be negative")
    if len(base_costs) < 2:
        raise ValueError("sensitivity analysis requires at least two venues")
    if len({item.venue for item in base_costs}) != len(base_costs):
        raise ValueError("venue cost profiles must be unique")

    fees = tuple(sorted(set(fee_tiers_bps)))
    transfers = tuple(sorted(set(transfer_costs_bps)))
    points: list[CrossVenueSensitivityPoint] = []

    for fee_bps in fees:
        costs = tuple(
            VenueCostProfile(
                venue=profile.venue,
                taker_fee_bps=fee_bps,
                execution_reserve_bps=profile.execution_reserve_bps,
                rebalance_reserve_bps=profile.rebalance_reserve_bps,
            )
            for profile in base_costs
        )
        for transfer_bps in transfers:
            summary = replay_cross_venue_capture(
                path,
                base_asset=base_asset,
                quote_asset=quote_asset,
                costs=costs,
                inventories=inventories,
                strategy_config=strategy_config,
                latency=latency,
                replay=replay,
                rebalance_config=RebalanceConfig(
                    base_transfer_cost_bps=transfer_bps,
                    quote_transfer_cost_bps=transfer_bps,
                ),
            )
            rebalance_cost = summary.rebalance_plan.total_estimated_cost_quote
            points.append(
                CrossVenueSensitivityPoint(
                    taker_fee_bps=fee_bps,
                    transfer_cost_bps=transfer_bps,
                    detected_signals=summary.detected_signals,
                    completed_trades=summary.completed_trades,
                    profitable_trades=summary.profitable_trades,
                    total_net_profit_quote=summary.total_net_profit_quote,
                    rebalance_cost_quote=rebalance_cost,
                    net_after_rebalance_quote=(
                        summary.total_net_profit_quote - rebalance_cost
                    ),
                    median_realized_edge_bps=summary.median_realized_edge_bps,
                    median_edge_decay_bps=summary.median_edge_decay_bps,
                )
            )

    return CrossVenueSensitivityReport(
        fee_tiers_bps=fees,
        transfer_costs_bps=transfers,
        points=tuple(points),
    )
