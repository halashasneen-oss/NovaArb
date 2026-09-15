from __future__ import annotations

from decimal import Decimal

from novaarb.shadow_acceptance import (
    ShadowAcceptanceCriteria,
    evaluate_shadow_acceptance,
)


def _metrics() -> dict[str, object]:
    return {
        "portfolio": {
            "ending_value_quote": "1000",
            "conservative_net_profit_quote": "25",
            "max_drawdown_quote": "10",
            "detected_signals": 500,
            "allocator_selected": 200,
            "inventory_rejections": 5,
            "risk_halts": 5,
            "executed_trades": 150,
            "profitable_trades": 100,
        },
        "venue_health": {
            "unhealthy_streams": 0,
        },
    }


def test_shadow_acceptance_passes_complete_evidence() -> None:
    report = evaluate_shadow_acceptance(
        _metrics(),
        observation_hours=Decimal("240"),
    )

    assert report.passed is True
    assert all(item.passed for item in report.checks)


def test_shadow_acceptance_fails_profit_drawdown_and_runtime() -> None:
    metrics = _metrics()
    portfolio = metrics["portfolio"]
    assert isinstance(portfolio, dict)
    portfolio["conservative_net_profit_quote"] = "-2"
    portfolio["max_drawdown_quote"] = "100"

    report = evaluate_shadow_acceptance(
        metrics,
        observation_hours=Decimal("24"),
    )

    assert report.passed is False
    by_name = {item.name: item for item in report.checks}
    assert by_name["observation_hours"].passed is False
    assert by_name["conservative_net_profit_quote"].passed is False
    assert by_name["max_drawdown_pct"].passed is False


def test_shadow_acceptance_criteria_are_configurable_for_research() -> None:
    criteria = ShadowAcceptanceCriteria(
        min_observation_hours=Decimal("1"),
        min_detected_signals=1,
        min_executed_trades=1,
        min_execution_survival_rate=Decimal("0"),
        min_profitable_trade_rate=Decimal("0"),
        min_net_profit_quote=Decimal("-10"),
        max_drawdown_pct=Decimal("1"),
        max_risk_halt_rate=Decimal("1"),
        max_inventory_rejection_rate=Decimal("1"),
        max_unhealthy_streams=5,
    )
    metrics = _metrics()
    metrics["venue_health"] = {"unhealthy_streams": 2}

    report = evaluate_shadow_acceptance(
        metrics,
        observation_hours=Decimal("1"),
        criteria=criteria,
    )

    assert report.passed is True
