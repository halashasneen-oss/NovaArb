from __future__ import annotations

import json
from decimal import Decimal

from novaarb.domain import MarketType
from novaarb.shadow import AssetBalance, AssetMark, PortfolioMark, VenueMark
from novaarb.shadow_metrics import build_shadow_metrics
from novaarb.shadow_portfolio import ShadowPortfolioSummary, ShadowSymbolSummary
from novaarb.venue_health import (
    VenueHealthReport,
    VenueHealthState,
    VenueSymbolHealth,
)


def _summary() -> ShadowPortfolioSummary:
    return ShadowPortfolioSummary(
        snapshots=120,
        detected_signals=12,
        allocator_selected=5,
        allocator_rejections=7,
        decayed_before_execution=1,
        inventory_rejections=1,
        risk_halts=2,
        executed_trades=3,
        profitable_trades=2,
        conservative_net_profit_quote=Decimal("4.25"),
        max_drawdown_quote=Decimal("1.50"),
        allocation_rejection_reasons={"insufficient_capital": 3},
        kill_switch_reasons={"data_unhealthy": 2},
        ending_balances=(
            AssetBalance("binance", "USDT", Decimal("600")),
            AssetBalance("bybit", "USDT", Decimal("400")),
        ),
        ending_mark=PortfolioMark(
            quote_asset="USDT",
            total_value_quote=Decimal("1000"),
            venue_values=(
                VenueMark("binance", Decimal("600")),
                VenueMark("bybit", Decimal("400")),
            ),
            asset_values=(AssetMark("USDT", Decimal("1000")),),
        ),
        symbols=(
            ShadowSymbolSummary(
                instrument="BTC/USDT:spot",
                detected_signals=7,
                allocator_selected=3,
                executed_trades=2,
                conservative_net_profit_quote=Decimal("3.5"),
                median_realized_edge_bps=Decimal("4.2"),
            ),
        ),
        trades=(),
    )


def _health() -> VenueHealthReport:
    return VenueHealthReport(
        source="capture.jsonl.gz",
        total_book_events=120,
        invalid_book_records=0,
        healthy_streams=1,
        degraded_streams=0,
        unhealthy_streams=0,
        streams=(
            VenueSymbolHealth(
                venue="binance",
                symbol="BTCUSDT",
                market=MarketType.SPOT,
                state=VenueHealthState.HEALTHY,
                event_count=120,
                duration_ms=10_000,
                event_rate_hz=Decimal("12"),
                feed_delay_p50_ms=10,
                feed_delay_p95_ms=20,
                feed_delay_p99_ms=30,
                feed_delay_max_ms=40,
                interarrival_p50_ms=80,
                interarrival_p95_ms=100,
                interarrival_p99_ms=120,
                interarrival_max_ms=150,
                stale_gap_count=0,
                stale_gap_ratio=Decimal("0"),
                out_of_order_count=0,
                out_of_order_ratio=Decimal("0"),
            ),
        ),
    )


def test_shadow_metrics_payload_is_stable_and_json_serializable() -> None:
    payload = build_shadow_metrics(_summary(), health=_health())

    assert payload["schema_version"] == 1
    assert payload["quote_asset"] == "USDT"
    assert payload["portfolio"]["ending_value_quote"] == "1000"
    assert payload["portfolio"]["conservative_net_profit_quote"] == "4.25"
    assert payload["venues"][0]["venue"] == "binance"
    assert payload["venues"][0]["weight"] == "0.6"
    assert payload["symbols"][0]["instrument"] == "BTC/USDT:spot"
    assert payload["venue_health"]["healthy_streams"] == 1
    assert payload["venue_health"]["streams"][0]["state"] == "healthy"

    encoded = json.dumps(payload, sort_keys=True)
    assert "BTC/USDT:spot" in encoded
    assert "Decimal" not in encoded


def test_shadow_metrics_can_omit_health_section() -> None:
    payload = build_shadow_metrics(_summary())

    assert "venue_health" not in payload
    assert payload["allocation_rejection_reasons"] == {"insufficient_capital": 3}
    assert payload["kill_switch_reasons"] == {"data_unhealthy": 2}
