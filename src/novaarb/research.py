from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any, TextIO

from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.risk import RiskReason


SCHEMA_VERSION = 1


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


class ResearchRecorder:
    """Append-only research log with optional gzip compression."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _open(self) -> TextIO:
        if self.path.suffix == ".gz":
            return gzip.open(self.path, "at", encoding="utf-8")
        return self.path.open("a", encoding="utf-8")

    def append_metadata(self, name: str, payload: object) -> None:
        self._append(
            {
                "v": SCHEMA_VERSION,
                "kind": "metadata",
                "name": name,
                "payload": payload,
            }
        )

    def append_book(self, snapshot: OrderBookSnapshot) -> None:
        self._append({"v": SCHEMA_VERSION, "kind": "book", "payload": snapshot})

    def append_funding(self, snapshot: object) -> None:
        """Persist a funding snapshot without importing exchange-specific models here."""
        self._append({"v": SCHEMA_VERSION, "kind": "funding", "payload": snapshot})

    def append_evaluation(self, event: object) -> None:
        self._append({"v": SCHEMA_VERSION, "kind": "evaluation", "payload": event})

    def _append(self, payload: object) -> None:
        line = json.dumps(payload, default=_json_default, separators=(",", ":"), sort_keys=True)
        with self._open() as handle:
            handle.write(line + "\n")


def iter_records(path: str | Path) -> Iterator[dict[str, Any]]:
    target = Path(path)
    opener = gzip.open if target.suffix == ".gz" else open
    with opener(target, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("v") != SCHEMA_VERSION:
                raise ValueError(f"unsupported research schema at line {line_number}")
            yield record


def snapshot_from_record(record: dict[str, Any]) -> OrderBookSnapshot:
    if record.get("kind") != "book":
        raise ValueError("record is not a book snapshot")
    payload = record["payload"]
    return OrderBookSnapshot(
        venue=str(payload["venue"]),
        symbol=str(payload["symbol"]),
        market=MarketType(payload["market"]),
        bids=tuple(
            BookLevel(Decimal(level["price"]), Decimal(level["quantity"]))
            for level in payload["bids"]
        ),
        asks=tuple(
            BookLevel(Decimal(level["price"]), Decimal(level["quantity"]))
            for level in payload["asks"]
        ),
        event_time_ms=int(payload["event_time_ms"]),
        received_time_ms=int(payload["received_time_ms"]),
    )


@dataclass(slots=True)
class OpportunityWindow:
    symbol: str
    buy_market: MarketType
    sell_market: MarketType
    started_ms: int
    last_seen_ms: int
    observations: int
    max_edge_bps: Decimal
    first_edge_bps: Decimal

    @property
    def duration_ms(self) -> int:
        return max(0, self.last_seen_ms - self.started_ms)


class OpportunityWindowTracker:
    """Collapses repeated approved observations into opportunity windows."""

    def __init__(self, *, gap_tolerance_ms: int = 500) -> None:
        self.gap_tolerance_ms = gap_tolerance_ms
        self.active: dict[tuple[str, MarketType, MarketType], OpportunityWindow] = {}
        self.closed: list[OpportunityWindow] = []

    def observe(
        self,
        *,
        symbol: str,
        buy_market: MarketType,
        sell_market: MarketType,
        timestamp_ms: int,
        approved: bool,
        edge_bps: Decimal,
    ) -> None:
        key = (symbol, buy_market, sell_market)
        current = self.active.get(key)
        if not approved:
            if current is not None:
                self.closed.append(current)
                del self.active[key]
            return

        if current is not None and timestamp_ms - current.last_seen_ms <= self.gap_tolerance_ms:
            current.last_seen_ms = timestamp_ms
            current.observations += 1
            current.max_edge_bps = max(current.max_edge_bps, edge_bps)
            return

        if current is not None:
            self.closed.append(current)
        self.active[key] = OpportunityWindow(
            symbol=symbol,
            buy_market=buy_market,
            sell_market=sell_market,
            started_ms=timestamp_ms,
            last_seen_ms=timestamp_ms,
            observations=1,
            max_edge_bps=edge_bps,
            first_edge_bps=edge_bps,
        )

    def finish(self) -> tuple[OpportunityWindow, ...]:
        self.closed.extend(self.active.values())
        self.active.clear()
        return tuple(self.closed)


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    snapshots: int
    evaluations: int
    approved_observations: int
    opportunity_windows: int
    symbols: tuple[str, ...]
    rejection_reasons: dict[str, int]
    max_edge_bps: Decimal
    median_window_ms: Decimal
    median_window_observations: Decimal
    median_feed_delay_ms: Decimal


class ReplayAccumulator:
    def __init__(self, *, gap_tolerance_ms: int = 500) -> None:
        self.snapshots = 0
        self.evaluations = 0
        self.approved = 0
        self.symbols: set[str] = set()
        self.rejections: Counter[str] = Counter()
        self.max_edge_bps = Decimal("0")
        self.feed_delays: list[int] = []
        self.windows = OpportunityWindowTracker(gap_tolerance_ms=gap_tolerance_ms)

    def add_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        self.snapshots += 1
        self.symbols.add(snapshot.symbol)
        self.feed_delays.append(max(0, snapshot.received_time_ms - snapshot.event_time_ms))

    def add_evaluations(self, events: Iterable[Any]) -> None:
        for event in events:
            self.evaluations += 1
            opportunity = event.opportunity
            risk = event.risk
            self.max_edge_bps = max(self.max_edge_bps, opportunity.costs.net_edge_bps)
            if risk.approved:
                self.approved += 1
            else:
                reason = (
                    risk.reason.value
                    if isinstance(risk.reason, RiskReason)
                    else str(risk.reason)
                )
                self.rejections[reason] += 1
            self.windows.observe(
                symbol=opportunity.symbol,
                buy_market=opportunity.buy_market,
                sell_market=opportunity.sell_market,
                timestamp_ms=opportunity.created_time_ms,
                approved=risk.approved,
                edge_bps=opportunity.costs.net_edge_bps,
            )

    def finish(self) -> ReplaySummary:
        windows = self.windows.finish()
        durations = [window.duration_ms for window in windows]
        observations = [window.observations for window in windows]
        return ReplaySummary(
            snapshots=self.snapshots,
            evaluations=self.evaluations,
            approved_observations=self.approved,
            opportunity_windows=len(windows),
            symbols=tuple(sorted(self.symbols)),
            rejection_reasons=dict(sorted(self.rejections.items())),
            max_edge_bps=self.max_edge_bps,
            median_window_ms=Decimal(str(median(durations))) if durations else Decimal("0"),
            median_window_observations=(
                Decimal(str(median(observations))) if observations else Decimal("0")
            ),
            median_feed_delay_ms=(
                Decimal(str(median(self.feed_delays))) if self.feed_delays else Decimal("0")
            ),
        )
