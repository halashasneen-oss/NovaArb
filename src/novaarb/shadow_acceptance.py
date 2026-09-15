from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from novaarb.domain import ZERO


@dataclass(frozen=True, slots=True)
class ShadowAcceptanceCriteria:
    min_observation_hours: Decimal = Decimal("168")
    min_detected_signals: int = 250
    min_executed_trades: int = 50
    min_execution_survival_rate: Decimal = Decimal("0.20")
    min_profitable_trade_rate: Decimal = Decimal("0.50")
    min_net_profit_quote: Decimal = Decimal("0.01")
    max_drawdown_pct: Decimal = Decimal("0.03")
    max_risk_halt_rate: Decimal = Decimal("0.05")
    max_inventory_rejection_rate: Decimal = Decimal("0.05")
    max_unhealthy_streams: int = 0

    def __post_init__(self) -> None:
        if self.min_observation_hours <= ZERO:
            raise ValueError("min_observation_hours must be positive")
        if self.min_detected_signals <= 0 or self.min_executed_trades <= 0:
            raise ValueError("minimum signal and trade counts must be positive")
        rates = (
            self.min_execution_survival_rate,
            self.min_profitable_trade_rate,
            self.max_drawdown_pct,
            self.max_risk_halt_rate,
            self.max_inventory_rejection_rate,
        )
        if any(value < ZERO or value > Decimal("1") for value in rates):
            raise ValueError("acceptance rates must be between 0 and 1")
        if self.max_unhealthy_streams < 0:
            raise ValueError("max_unhealthy_streams cannot be negative")


@dataclass(frozen=True, slots=True)
class ShadowAcceptanceCheck:
    name: str
    observed: Decimal
    threshold: Decimal
    comparator: str
    passed: bool


@dataclass(frozen=True, slots=True)
class ShadowAcceptanceReport:
    passed: bool
    checks: tuple[ShadowAcceptanceCheck, ...]


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    return Decimal(str(value))


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return ZERO
    return Decimal(numerator) / Decimal(denominator)


def _check_min(name: str, observed: Decimal, threshold: Decimal) -> ShadowAcceptanceCheck:
    return ShadowAcceptanceCheck(name, observed, threshold, ">=", observed >= threshold)


def _check_max(name: str, observed: Decimal, threshold: Decimal) -> ShadowAcceptanceCheck:
    return ShadowAcceptanceCheck(name, observed, threshold, "<=", observed <= threshold)


def evaluate_shadow_acceptance(
    metrics: Mapping[str, Any],
    *,
    observation_hours: Decimal,
    criteria: ShadowAcceptanceCriteria | None = None,
) -> ShadowAcceptanceReport:
    """Evaluate replay/shadow metrics against an explicit pre-live evidence gate.

    Passing this gate is necessary evidence for future engineering review; it is not a profit
    guarantee and it does not enable authenticated trading by itself.
    """

    resolved = criteria or ShadowAcceptanceCriteria()
    portfolio = metrics.get("portfolio")
    if not isinstance(portfolio, Mapping):
        raise ValueError("shadow metrics payload is missing portfolio metrics")

    detected = int(portfolio.get("detected_signals", 0) or 0)
    selected = int(portfolio.get("allocator_selected", 0) or 0)
    executed = int(portfolio.get("executed_trades", 0) or 0)
    profitable = int(portfolio.get("profitable_trades", 0) or 0)
    risk_halts = int(portfolio.get("risk_halts", 0) or 0)
    inventory_rejections = int(portfolio.get("inventory_rejections", 0) or 0)
    net_profit = _decimal(portfolio.get("conservative_net_profit_quote"))
    drawdown = _decimal(portfolio.get("max_drawdown_quote"))
    ending_value = _decimal(portfolio.get("ending_value_quote"))

    execution_survival = _ratio(executed, selected)
    profitable_rate = _ratio(profitable, executed)
    risk_halt_rate = _ratio(risk_halts, detected)
    inventory_rejection_rate = _ratio(inventory_rejections, detected)
    drawdown_pct = drawdown / ending_value if ending_value > ZERO else Decimal("1")

    health = metrics.get("venue_health")
    unhealthy_streams = 0
    if isinstance(health, Mapping):
        unhealthy_streams = int(health.get("unhealthy_streams", 0) or 0)

    checks = (
        _check_min(
            "observation_hours",
            observation_hours,
            resolved.min_observation_hours,
        ),
        _check_min(
            "detected_signals",
            Decimal(detected),
            Decimal(resolved.min_detected_signals),
        ),
        _check_min(
            "executed_trades",
            Decimal(executed),
            Decimal(resolved.min_executed_trades),
        ),
        _check_min(
            "execution_survival_rate",
            execution_survival,
            resolved.min_execution_survival_rate,
        ),
        _check_min(
            "profitable_trade_rate",
            profitable_rate,
            resolved.min_profitable_trade_rate,
        ),
        _check_min(
            "conservative_net_profit_quote",
            net_profit,
            resolved.min_net_profit_quote,
        ),
        _check_max("max_drawdown_pct", drawdown_pct, resolved.max_drawdown_pct),
        _check_max("risk_halt_rate", risk_halt_rate, resolved.max_risk_halt_rate),
        _check_max(
            "inventory_rejection_rate",
            inventory_rejection_rate,
            resolved.max_inventory_rejection_rate,
        ),
        _check_max(
            "unhealthy_streams",
            Decimal(unhealthy_streams),
            Decimal(resolved.max_unhealthy_streams),
        ),
    )
    return ShadowAcceptanceReport(
        passed=all(item.passed for item in checks),
        checks=checks,
    )
