from __future__ import annotations

from decimal import Decimal
from typing import Any

from novaarb.shadow_portfolio import ShadowPortfolioSummary
from novaarb.venue_health import VenueHealthReport


SHADOW_METRICS_SCHEMA_VERSION = 1


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def build_shadow_metrics(
    summary: ShadowPortfolioSummary,
    *,
    health: VenueHealthReport | None = None,
) -> dict[str, Any]:
    """Return a stable JSON-ready metrics payload for operators and dashboards."""

    total_value = summary.ending_mark.total_value_quote
    venue_values = [
        {
            "venue": item.venue,
            "value_quote": _decimal(item.value_quote),
            "weight": _decimal(item.value_quote / total_value) if total_value else "0",
        }
        for item in summary.ending_mark.venue_values
    ]
    asset_values = [
        {
            "asset": item.asset,
            "value_quote": _decimal(item.value_quote),
            "weight": _decimal(item.value_quote / total_value) if total_value else "0",
        }
        for item in summary.ending_mark.asset_values
    ]
    payload: dict[str, Any] = {
        "schema_version": SHADOW_METRICS_SCHEMA_VERSION,
        "quote_asset": summary.ending_mark.quote_asset,
        "portfolio": {
            "ending_value_quote": _decimal(total_value),
            "conservative_net_profit_quote": _decimal(
                summary.conservative_net_profit_quote
            ),
            "max_drawdown_quote": _decimal(summary.max_drawdown_quote),
            "detected_signals": summary.detected_signals,
            "allocator_selected": summary.allocator_selected,
            "allocator_rejections": summary.allocator_rejections,
            "decayed_before_execution": summary.decayed_before_execution,
            "inventory_rejections": summary.inventory_rejections,
            "risk_halts": summary.risk_halts,
            "executed_trades": summary.executed_trades,
            "profitable_trades": summary.profitable_trades,
        },
        "venues": venue_values,
        "assets": asset_values,
        "allocation_rejection_reasons": summary.allocation_rejection_reasons,
        "kill_switch_reasons": summary.kill_switch_reasons,
        "symbols": [
            {
                "instrument": item.instrument,
                "detected_signals": item.detected_signals,
                "allocator_selected": item.allocator_selected,
                "executed_trades": item.executed_trades,
                "conservative_net_profit_quote": _decimal(
                    item.conservative_net_profit_quote
                ),
                "median_realized_edge_bps": _decimal(
                    item.median_realized_edge_bps
                ),
            }
            for item in summary.symbols
        ],
    }
    if health is not None:
        payload["venue_health"] = {
            "total_book_events": health.total_book_events,
            "invalid_book_records": health.invalid_book_records,
            "healthy_streams": health.healthy_streams,
            "degraded_streams": health.degraded_streams,
            "unhealthy_streams": health.unhealthy_streams,
            "streams": [
                {
                    "venue": item.venue,
                    "symbol": item.symbol,
                    "market": item.market.value,
                    "state": item.state.value,
                    "event_count": item.event_count,
                    "event_rate_hz": _decimal(item.event_rate_hz),
                    "feed_delay_p50_ms": item.feed_delay_p50_ms,
                    "feed_delay_p95_ms": item.feed_delay_p95_ms,
                    "feed_delay_p99_ms": item.feed_delay_p99_ms,
                    "interarrival_p95_ms": item.interarrival_p95_ms,
                    "stale_gap_ratio": _decimal(item.stale_gap_ratio),
                    "out_of_order_ratio": _decimal(item.out_of_order_ratio),
                }
                for item in health.streams
            ],
        }
    return payload
