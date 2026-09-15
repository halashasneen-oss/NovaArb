from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from novaarb.cross_venue import CrossVenueConfig, CrossVenueStrategy, VenueCostProfile
from novaarb.domain import MarketType, ZERO
from novaarb.research import iter_records, snapshot_from_record
from novaarb.venue import Instrument, NormalizedBook


@dataclass(frozen=True, slots=True)
class SurvivalPoint:
    threshold_ms: int
    surviving_windows: int
    survival_rate: Decimal


@dataclass(frozen=True, slots=True)
class DirectionSurvival:
    buy_venue: str
    sell_venue: str
    window_count: int
    median_duration_ms: Decimal
    max_duration_ms: int
    median_peak_edge_bps: Decimal
    survival_curve: tuple[SurvivalPoint, ...]


@dataclass(frozen=True, slots=True)
class CrossVenueSurvivalReport:
    windows: int
    median_duration_ms: Decimal
    max_duration_ms: int
    median_peak_edge_bps: Decimal
    survival_curve: tuple[SurvivalPoint, ...]
    directions: tuple[DirectionSurvival, ...]


@dataclass(slots=True)
class _Window:
    buy_venue: str
    sell_venue: str
    started_ms: int
    last_seen_ms: int
    observations: int
    peak_edge_bps: Decimal

    @property
    def duration_ms(self) -> int:
        return max(0, self.last_seen_ms - self.started_ms)


def _median_decimal(values: list[int | Decimal]) -> Decimal:
    if not values:
        return ZERO
    return Decimal(str(median(values)))


def _survival_points(
    windows: list[_Window],
    thresholds_ms: tuple[int, ...],
) -> tuple[SurvivalPoint, ...]:
    total = len(windows)
    return tuple(
        SurvivalPoint(
            threshold_ms=threshold,
            surviving_windows=sum(1 for window in windows if window.duration_ms >= threshold),
            survival_rate=(
                Decimal(sum(1 for window in windows if window.duration_ms >= threshold))
                / Decimal(total)
                if total
                else ZERO
            ),
        )
        for threshold in thresholds_ms
    )


def analyze_cross_venue_survival(
    path: str,
    *,
    base_asset: str,
    quote_asset: str,
    costs: tuple[VenueCostProfile, ...],
    strategy_config: CrossVenueConfig | None = None,
    gap_tolerance_ms: int = 250,
    thresholds_ms: tuple[int, ...] = (0, 25, 50, 100, 200, 500, 1_000),
) -> CrossVenueSurvivalReport:
    """Measure how long approved cross-venue dislocations remain observable.

    Windows are derived from public-book observations only. They are intentionally separate from
    fill replay: this report measures signal persistence, while delayed replay measures whether
    an observed signal remains executable after latency.
    """

    if gap_tolerance_ms < 0:
        raise ValueError("gap_tolerance_ms cannot be negative")
    if not thresholds_ms or any(value < 0 for value in thresholds_ms):
        raise ValueError("survival thresholds must be non-negative and non-empty")
    thresholds = tuple(sorted(set(thresholds_ms)))
    if len(costs) < 2:
        raise ValueError("survival analysis requires at least two venues")
    venue_names = {profile.venue for profile in costs}
    if len(venue_names) != len(costs):
        raise ValueError("venue cost profiles must be unique")

    instrument = Instrument(base_asset, quote_asset, MarketType.SPOT)
    books: list[NormalizedBook] = []
    for record in iter_records(path):
        if record.get("kind") != "book":
            continue
        snapshot = snapshot_from_record(record)
        if snapshot.market is not MarketType.SPOT or snapshot.venue not in venue_names:
            continue
        books.append(
            NormalizedBook(
                instrument=instrument,
                venue_symbol=snapshot.symbol,
                snapshot=snapshot,
            )
        )
    if not books:
        raise ValueError("capture contains no mapped Spot books")
    books.sort(key=lambda item: item.snapshot.received_time_ms)

    strategy = CrossVenueStrategy(
        config=strategy_config or CrossVenueConfig(),
        costs=costs,
    )
    latest: dict[str, NormalizedBook] = {}
    active: dict[tuple[str, str], _Window] = {}
    closed: list[_Window] = []

    def close_pair(first: str, second: str) -> None:
        for key in ((first, second), (second, first)):
            current = active.pop(key, None)
            if current is not None:
                closed.append(current)

    def observe(*, buy: str, sell: str, timestamp_ms: int, edge_bps: Decimal) -> None:
        opposite = active.pop((sell, buy), None)
        if opposite is not None:
            closed.append(opposite)
        key = (buy, sell)
        current = active.get(key)
        if current is not None and timestamp_ms - current.last_seen_ms <= gap_tolerance_ms:
            current.last_seen_ms = timestamp_ms
            current.observations += 1
            current.peak_edge_bps = max(current.peak_edge_bps, edge_bps)
            return
        if current is not None:
            closed.append(current)
        active[key] = _Window(
            buy_venue=buy,
            sell_venue=sell,
            started_ms=timestamp_ms,
            last_seen_ms=timestamp_ms,
            observations=1,
            peak_edge_bps=edge_bps,
        )

    for current in books:
        latest[current.venue] = current
        for other_venue, other in tuple(latest.items()):
            if other_venue == current.venue:
                continue
            timestamp_ms = max(
                current.snapshot.received_time_ms,
                other.snapshot.received_time_ms,
            )
            result = strategy.best_direction(
                current,
                other,
                now_ms=timestamp_ms,
            )
            if result is None:
                close_pair(current.venue, other_venue)
                continue
            opportunity, decision = result
            if not decision.approved:
                close_pair(current.venue, other_venue)
                continue
            observe(
                buy=opportunity.buy_venue,
                sell=opportunity.sell_venue,
                timestamp_ms=timestamp_ms,
                edge_bps=opportunity.net_edge_bps,
            )

    closed.extend(active.values())
    if not closed:
        return CrossVenueSurvivalReport(
            windows=0,
            median_duration_ms=ZERO,
            max_duration_ms=0,
            median_peak_edge_bps=ZERO,
            survival_curve=_survival_points([], thresholds),
            directions=(),
        )

    by_direction: dict[tuple[str, str], list[_Window]] = {}
    for window in closed:
        by_direction.setdefault((window.buy_venue, window.sell_venue), []).append(window)
    directions = tuple(
        DirectionSurvival(
            buy_venue=buy,
            sell_venue=sell,
            window_count=len(items),
            median_duration_ms=_median_decimal([item.duration_ms for item in items]),
            max_duration_ms=max(item.duration_ms for item in items),
            median_peak_edge_bps=_median_decimal([item.peak_edge_bps for item in items]),
            survival_curve=_survival_points(items, thresholds),
        )
        for (buy, sell), items in sorted(by_direction.items())
    )
    return CrossVenueSurvivalReport(
        windows=len(closed),
        median_duration_ms=_median_decimal([item.duration_ms for item in closed]),
        max_duration_ms=max(item.duration_ms for item in closed),
        median_peak_edge_bps=_median_decimal([item.peak_edge_bps for item in closed]),
        survival_curve=_survival_points(closed, thresholds),
        directions=directions,
    )
