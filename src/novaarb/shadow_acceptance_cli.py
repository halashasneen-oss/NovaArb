from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from novaarb.shadow_acceptance import (
    ShadowAcceptanceCriteria,
    evaluate_shadow_acceptance,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate NovaArb shadow evidence against explicit pre-live criteria"
    )
    parser.add_argument("metrics", help="JSON metrics produced by novaarb-shadow-replay")
    parser.add_argument("--observation-hours", type=Decimal, required=True)
    parser.add_argument("--min-observation-hours", type=Decimal, default=Decimal("168"))
    parser.add_argument("--min-detected-signals", type=int, default=250)
    parser.add_argument("--min-executed-trades", type=int, default=50)
    parser.add_argument(
        "--min-execution-survival-rate",
        type=Decimal,
        default=Decimal("0.20"),
    )
    parser.add_argument(
        "--min-profitable-trade-rate",
        type=Decimal,
        default=Decimal("0.50"),
    )
    parser.add_argument("--min-net-profit", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--max-drawdown-pct", type=Decimal, default=Decimal("0.03"))
    parser.add_argument("--max-risk-halt-rate", type=Decimal, default=Decimal("0.05"))
    parser.add_argument(
        "--max-inventory-rejection-rate",
        type=Decimal,
        default=Decimal("0.05"),
    )
    parser.add_argument("--max-unhealthy-streams", type=int, default=0)
    parser.add_argument("--output", default=None)
    return parser


def main() -> None:
    args = _parser().parse_args()
    with open(args.metrics, encoding="utf-8") as handle:
        metrics = json.load(handle)
    criteria = ShadowAcceptanceCriteria(
        min_observation_hours=args.min_observation_hours,
        min_detected_signals=args.min_detected_signals,
        min_executed_trades=args.min_executed_trades,
        min_execution_survival_rate=args.min_execution_survival_rate,
        min_profitable_trade_rate=args.min_profitable_trade_rate,
        min_net_profit_quote=args.min_net_profit,
        max_drawdown_pct=args.max_drawdown_pct,
        max_risk_halt_rate=args.max_risk_halt_rate,
        max_inventory_rejection_rate=args.max_inventory_rejection_rate,
        max_unhealthy_streams=args.max_unhealthy_streams,
    )
    report = evaluate_shadow_acceptance(
        metrics,
        observation_hours=args.observation_hours,
        criteria=criteria,
    )
    encoded = json.dumps(asdict(report), indent=2, sort_keys=True, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)
    if not report.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
