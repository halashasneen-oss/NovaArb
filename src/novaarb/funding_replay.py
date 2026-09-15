from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any

from novaarb.funding import (
    FundingCarryConfig,
    FundingCarryReason,
    FundingCarryScanner,
    FundingSnapshot,
)
from novaarb.research import iter_records, snapshot_from_record


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def funding_from_record(record: dict[str, Any]) -> FundingSnapshot:
    if record.get("kind") != "funding":
        raise ValueError("record is not a funding snapshot")
    payload = record["payload"]
    return FundingSnapshot(
        symbol=str(payload["symbol"]),
        funding_rate=_decimal(payload["funding_rate"]),
        next_funding_time_ms=int(payload["next_funding_time_ms"]),
        mark_price=_decimal(payload["mark_price"]),
        index_price=_decimal(payload["index_price"]),
        received_time_ms=int(payload["received_time_ms"]),
    )


def _parse_config(payload: dict[str, Any]) -> FundingCarryConfig:
    return FundingCarryConfig(
        target_notional_usdt=_decimal(payload["target_notional_usdt"]),
        funding_intervals=int(payload["funding_intervals"]),
        funding_haircut=_decimal(payload["funding_haircut"]),
        spot_taker_fee_bps=_decimal(payload["spot_taker_fee_bps"]),
        futures_taker_fee_bps=_decimal(payload["futures_taker_fee_bps"]),
        exit_market_reserve_bps=_decimal(payload["exit_market_reserve_bps"]),
        basis_risk_reserve_bps=_decimal(payload["basis_risk_reserve_bps"]),
        min_net_edge_bps=_decimal(payload["min_net_edge_bps"]),
        max_book_age_ms=int(payload["max_book_age_ms"]),
        max_book_skew_ms=int(payload["max_book_skew_ms"]),
        max_funding_age_ms=int(payload["max_funding_age_ms"]),
    )


def load_funding_session(
    records: list[dict[str, Any]],
) -> tuple[tuple[str, ...], FundingCarryConfig]:
    for record in records:
        if record.get("kind") != "metadata" or record.get("name") != "funding_session":
            continue
        payload = record["payload"]
        symbols = tuple(str(symbol).upper() for symbol in payload["symbols"])
        return symbols, _parse_config(payload["config"])
    raise ValueError("funding_session metadata not found in research log")


@dataclass(slots=True)
class FundingWindow:
    symbol: str
    started_ms: int
    last_seen_ms: int
    observations: int
    max_projected_edge_bps: Decimal

    @property
    def duration_ms(self) -> int:
        return max(0, self.last_seen_ms - self.started_ms)


class FundingWindowTracker:
    def __init__(self, *, gap_tolerance_ms: int = 5_000) -> None:
        self.gap_tolerance_ms = gap_tolerance_ms
        self.active: dict[str, FundingWindow] = {}
        self.closed: list[FundingWindow] = []

    def observe(
        self,
        *,
        symbol: str,
        timestamp_ms: int,
        approved: bool,
        projected_edge_bps: Decimal,
    ) -> None:
        current = self.active.get(symbol)
        if not approved:
            if current is not None:
                self.closed.append(current)
                del self.active[symbol]
            return
        if current is not None and timestamp_ms - current.last_seen_ms <= self.gap_tolerance_ms:
            current.last_seen_ms = timestamp_ms
            current.observations += 1
            current.max_projected_edge_bps = max(
                current.max_projected_edge_bps,
                projected_edge_bps,
            )
            return
        if current is not None:
            self.closed.append(current)
        self.active[symbol] = FundingWindow(
            symbol=symbol,
            started_ms=timestamp_ms,
            last_seen_ms=timestamp_ms,
            observations=1,
            max_projected_edge_bps=projected_edge_bps,
        )

    def finish(self) -> tuple[FundingWindow, ...]:
        self.closed.extend(self.active.values())
        self.active.clear()
        return tuple(self.closed)


@dataclass(frozen=True, slots=True)
class FundingSymbolStats:
    symbol: str
    evaluations: int
    approved_observations: int
    windows: int
    max_projected_edge_bps: Decimal
    max_funding_rate_bps: Decimal


@dataclass(frozen=True, slots=True)
class FundingReplaySummary:
    book_snapshots: int
    funding_updates: int
    evaluations: int
    approved_observations: int
    opportunity_windows: int
    median_window_ms: Decimal
    max_projected_edge_bps: Decimal
    rejection_reasons: dict[str, int]
    symbols: tuple[FundingSymbolStats, ...]


def replay_funding_log(
    path: str,
    *,
    gap_tolerance_ms: int = 5_000,
) -> FundingReplaySummary:
    records = list(iter_records(path))
    symbols, config = load_funding_session(records)
    scanner = FundingCarryScanner(symbols=symbols, config=config)
    tracker = FundingWindowTracker(gap_tolerance_ms=gap_tolerance_ms)
    rejections: Counter[str] = Counter()
    evaluations: Counter[str] = Counter()
    approved: Counter[str] = Counter()
    windows_by_symbol: Counter[str] = Counter()
    max_edge: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    max_funding: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    book_snapshots = 0
    funding_updates = 0

    for record in records:
        kind = record.get("kind")
        if kind == "funding":
            funding = funding_from_record(record)
            funding_updates += 1
            scanner.update_funding((funding,))
            max_funding[funding.symbol] = max(
                max_funding[funding.symbol],
                funding.funding_rate_bps,
            )
            continue
        if kind != "book":
            continue

        book = snapshot_from_record(record)
        book_snapshots += 1
        event = scanner.process_snapshot(book, now_ms=book.received_time_ms)
        if event is None:
            continue
        opportunity = event.opportunity
        symbol = opportunity.symbol
        evaluations[symbol] += 1
        max_edge[symbol] = max(max_edge[symbol], opportunity.expected_net_edge_bps)
        if event.decision.approved:
            approved[symbol] += 1
        else:
            reason = event.decision.reason
            key = reason.value if isinstance(reason, FundingCarryReason) else str(reason)
            rejections[key] += 1
        tracker.observe(
            symbol=symbol,
            timestamp_ms=opportunity.created_time_ms,
            approved=event.decision.approved,
            projected_edge_bps=opportunity.expected_net_edge_bps,
        )

    windows = tracker.finish()
    for window in windows:
        windows_by_symbol[window.symbol] += 1
    durations = [window.duration_ms for window in windows]
    symbol_names = sorted(set(symbols) | set(evaluations) | set(max_funding))
    symbol_stats = tuple(
        FundingSymbolStats(
            symbol=symbol,
            evaluations=evaluations[symbol],
            approved_observations=approved[symbol],
            windows=windows_by_symbol[symbol],
            max_projected_edge_bps=max_edge[symbol],
            max_funding_rate_bps=max_funding[symbol],
        )
        for symbol in symbol_names
    )
    return FundingReplaySummary(
        book_snapshots=book_snapshots,
        funding_updates=funding_updates,
        evaluations=sum(evaluations.values()),
        approved_observations=sum(approved.values()),
        opportunity_windows=len(windows),
        median_window_ms=Decimal(str(median(durations))) if durations else Decimal("0"),
        max_projected_edge_bps=max(max_edge.values(), default=Decimal("0")),
        rejection_reasons=dict(sorted(rejections.items())),
        symbols=symbol_stats,
    )
