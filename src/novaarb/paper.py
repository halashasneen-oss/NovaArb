from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from novaarb.execution import BookTimeline, LatencyProfile, SequentialTriangleSimulator
from novaarb.recovery import EmergencyUnwinder, RecoveryPolicy
from novaarb.research import iter_records, snapshot_from_record
from novaarb.triangle_replay import load_triangle_session
from novaarb.triangle_scanner import TriangularScanner


@dataclass(frozen=True, slots=True)
class PaperPortfolioConfig:
    initial_balance: Decimal = Decimal("1000")
    route_cooldown_ms: int = 500
    attempt_recovery: bool = True
    halt_on_unrecovered_leg_risk: bool = True
    recovery_delay_ms: int = 50
    recovery_book_wait_ms: int = 250

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.route_cooldown_ms < 0:
            raise ValueError("route_cooldown_ms cannot be negative")
        if self.recovery_delay_ms < 0 or self.recovery_book_wait_ms < 0:
            raise ValueError("recovery timing cannot be negative")


@dataclass(frozen=True, slots=True)
class PaperTrade:
    route_id: str
    signal_time_ms: int
    completion_ms: int
    net_profit: Decimal
    realized_edge_bps: Decimal
    balance_after: Decimal
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class PaperPortfolioSummary:
    initial_balance: Decimal
    final_balance: Decimal
    trades: int
    profitable_trades: int
    losing_trades: int
    skipped_busy: int
    skipped_cooldown: int
    failed_executions: int
    recovered_exposures: int
    unrecovered_exposures: int
    halted_on_leg_risk: bool
    total_net_profit: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    profit_factor: Decimal
    trade_log: tuple[PaperTrade, ...]


class PaperLedger:
    def __init__(self, initial_balance: Decimal) -> None:
        if initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.peak = initial_balance
        self.max_drawdown_pct = Decimal("0")
        self.gross_profit = Decimal("0")
        self.gross_loss = Decimal("0")
        self.trades: list[PaperTrade] = []

    def apply(
        self,
        *,
        route_id: str,
        signal_time_ms: int,
        completion_ms: int,
        net_profit: Decimal,
        realized_edge_bps: Decimal,
        recovered: bool = False,
    ) -> None:
        self.balance += net_profit
        if net_profit > 0:
            self.gross_profit += net_profit
        elif net_profit < 0:
            self.gross_loss += -net_profit
        self.peak = max(self.peak, self.balance)
        if self.peak > 0:
            drawdown = (self.peak - self.balance) / self.peak * Decimal("100")
            self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)
        self.trades.append(
            PaperTrade(
                route_id=route_id,
                signal_time_ms=signal_time_ms,
                completion_ms=completion_ms,
                net_profit=net_profit,
                realized_edge_bps=realized_edge_bps,
                balance_after=self.balance,
                recovered=recovered,
            )
        )

    @property
    def profit_factor(self) -> Decimal:
        if self.gross_loss == 0:
            return Decimal("Infinity") if self.gross_profit > 0 else Decimal("0")
        return self.gross_profit / self.gross_loss


def simulate_triangle_portfolio(
    path: str,
    *,
    latency: LatencyProfile,
    portfolio: PaperPortfolioConfig = PaperPortfolioConfig(),
) -> PaperPortfolioSummary:
    records = list(iter_records(path))
    rules, routes, config = load_triangle_session(records)
    snapshots = [
        snapshot_from_record(record)
        for record in records
        if record.get("kind") == "book"
    ]
    timeline = BookTimeline(snapshots)
    scanner = TriangularScanner(rules=rules, routes=routes, config=config)
    simulator = SequentialTriangleSimulator(
        rules=rules,
        taker_fee_bps=config.taker_fee_bps,
        latency=latency,
    )
    unwinder = EmergencyUnwinder(
        rules=rules,
        taker_fee_bps=config.taker_fee_bps,
        policy=RecoveryPolicy(
            delay_ms=portfolio.recovery_delay_ms,
            max_book_wait_ms=portfolio.recovery_book_wait_ms,
        ),
    )
    ledger = PaperLedger(portfolio.initial_balance)
    busy_until_ms = 0
    last_route_trade_ms: dict[str, int] = {}
    skipped_busy = 0
    skipped_cooldown = 0
    failed_executions = 0
    recovered_exposures = 0
    unrecovered_exposures = 0
    halted_on_leg_risk = False

    for snapshot in snapshots:
        if halted_on_leg_risk:
            break
        events = scanner.process_snapshot(snapshot, now_ms=snapshot.received_time_ms)
        for event in events:
            if halted_on_leg_risk:
                break
            if not event.risk.approved:
                continue
            signal_ms = event.opportunity.created_time_ms
            route_id = event.opportunity.route.route_id
            if signal_ms < busy_until_ms:
                skipped_busy += 1
                continue
            last_route = last_route_trade_ms.get(route_id)
            if last_route is not None and signal_ms - last_route < portfolio.route_cooldown_ms:
                skipped_cooldown += 1
                continue
            if ledger.balance < event.opportunity.starting_amount:
                continue

            result = simulator.simulate(event.opportunity, timeline)
            if result.completed:
                ledger.apply(
                    route_id=route_id,
                    signal_time_ms=signal_ms,
                    completion_ms=result.completion_ms,
                    net_profit=result.net_profit,
                    realized_edge_bps=result.realized_edge_bps,
                )
                busy_until_ms = result.completion_ms
                last_route_trade_ms[route_id] = signal_ms
                continue

            failed_executions += 1
            if not result.legs:
                continue

            if portfolio.attempt_recovery:
                recovery = unwinder.recover(event.opportunity, result, timeline)
                if recovery.recovered:
                    recovered_exposures += 1
                    ledger.apply(
                        route_id=route_id,
                        signal_time_ms=signal_ms,
                        completion_ms=recovery.completion_ms,
                        net_profit=recovery.net_profit,
                        realized_edge_bps=recovery.realized_edge_bps,
                        recovered=True,
                    )
                    busy_until_ms = recovery.completion_ms
                    last_route_trade_ms[route_id] = signal_ms
                    continue

            unrecovered_exposures += 1
            if portfolio.halt_on_unrecovered_leg_risk:
                halted_on_leg_risk = True

    profitable = sum(trade.net_profit > 0 for trade in ledger.trades)
    losing = sum(trade.net_profit <= 0 for trade in ledger.trades)
    total_net = ledger.balance - portfolio.initial_balance
    return PaperPortfolioSummary(
        initial_balance=portfolio.initial_balance,
        final_balance=ledger.balance,
        trades=len(ledger.trades),
        profitable_trades=profitable,
        losing_trades=losing,
        skipped_busy=skipped_busy,
        skipped_cooldown=skipped_cooldown,
        failed_executions=failed_executions,
        recovered_exposures=recovered_exposures,
        unrecovered_exposures=unrecovered_exposures,
        halted_on_leg_risk=halted_on_leg_risk,
        total_net_profit=total_net,
        return_pct=total_net / portfolio.initial_balance * Decimal("100"),
        max_drawdown_pct=ledger.max_drawdown_pct,
        profit_factor=ledger.profit_factor,
        trade_log=tuple(ledger.trades),
    )
