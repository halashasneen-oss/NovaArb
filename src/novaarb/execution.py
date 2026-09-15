from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import TEN_THOUSAND, ZERO, OrderBookSnapshot
from novaarb.symbols import SymbolRules
from novaarb.triangular import (
    ConversionError,
    ConversionFill,
    SpotConverter,
    TriangularOpportunity,
)


class ExecutionFailure(StrEnum):
    NONE = "none"
    MISSING_FUTURE_BOOK = "missing_future_book"
    BOOK_WAIT_EXCEEDED = "book_wait_exceeded"
    CONVERSION_ERROR = "conversion_error"


@dataclass(frozen=True, slots=True)
class LatencyProfile:
    """Deterministic timing assumptions for sequential paper execution."""

    name: str
    detection_to_first_leg_ms: int
    inter_leg_ms: int
    max_book_wait_ms: int = 250

    def __post_init__(self) -> None:
        if self.detection_to_first_leg_ms < 0:
            raise ValueError("detection_to_first_leg_ms cannot be negative")
        if self.inter_leg_ms < 0:
            raise ValueError("inter_leg_ms cannot be negative")
        if self.max_book_wait_ms < 0:
            raise ValueError("max_book_wait_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class ExecutedLeg:
    index: int
    scheduled_ms: int
    book_received_ms: int
    wait_ms: int
    fill: ConversionFill


@dataclass(frozen=True, slots=True)
class SequentialFillResult:
    route_id: str
    signal_time_ms: int
    profile_name: str
    completed: bool
    failure: ExecutionFailure
    failure_leg_index: int | None
    starting_amount: Decimal
    final_amount: Decimal
    net_profit: Decimal
    detected_edge_bps: Decimal
    realized_edge_bps: Decimal
    edge_decay_bps: Decimal
    completion_ms: int
    exposure_asset: str | None
    exposure_amount: Decimal
    legs: tuple[ExecutedLeg, ...]

    @property
    def duration_ms(self) -> int:
        return max(0, self.completion_ms - self.signal_time_ms)


class BookTimeline:
    """Indexed future order books used by deterministic execution replay."""

    def __init__(self, snapshots: Iterable[OrderBookSnapshot]) -> None:
        grouped: dict[str, list[OrderBookSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.symbol, []).append(snapshot)
        self._books: dict[str, tuple[OrderBookSnapshot, ...]] = {}
        self._times: dict[str, tuple[int, ...]] = {}
        for symbol, values in grouped.items():
            ordered = tuple(sorted(values, key=lambda item: item.received_time_ms))
            self._books[symbol] = ordered
            self._times[symbol] = tuple(item.received_time_ms for item in ordered)

    def at_or_after(
        self,
        symbol: str,
        scheduled_ms: int,
        *,
        max_wait_ms: int,
    ) -> tuple[OrderBookSnapshot | None, ExecutionFailure]:
        times = self._times.get(symbol)
        books = self._books.get(symbol)
        if not times or books is None:
            return None, ExecutionFailure.MISSING_FUTURE_BOOK
        index = bisect_left(times, scheduled_ms)
        if index >= len(books):
            return None, ExecutionFailure.MISSING_FUTURE_BOOK
        book = books[index]
        if book.received_time_ms - scheduled_ms > max_wait_ms:
            return None, ExecutionFailure.BOOK_WAIT_EXCEEDED
        return book, ExecutionFailure.NONE


class SequentialTriangleSimulator:
    """Re-prices every triangular leg against a later, actually observed book."""

    def __init__(
        self,
        *,
        rules: tuple[SymbolRules, ...],
        taker_fee_bps: Decimal,
        latency: LatencyProfile,
    ) -> None:
        self.rules_by_symbol = {rule.symbol: rule for rule in rules}
        self.converter = SpotConverter(taker_fee_bps=taker_fee_bps)
        self.latency = latency

    def simulate(
        self,
        opportunity: TriangularOpportunity,
        timeline: BookTimeline,
    ) -> SequentialFillResult:
        route = opportunity.route
        assets = route.assets
        amount = opportunity.starting_amount
        executed: list[ExecutedLeg] = []
        completion_ms = opportunity.created_time_ms

        for index, symbol in enumerate(route.symbols):
            scheduled_ms = (
                opportunity.created_time_ms
                + self.latency.detection_to_first_leg_ms
                + index * self.latency.inter_leg_ms
            )
            book, failure = timeline.at_or_after(
                symbol,
                scheduled_ms,
                max_wait_ms=self.latency.max_book_wait_ms,
            )
            if book is None:
                return self._failure_result(
                    opportunity=opportunity,
                    failure=failure,
                    failure_leg_index=index,
                    completion_ms=scheduled_ms,
                    amount=amount,
                    exposure_asset=assets[index],
                    executed=executed,
                )

            rules = self.rules_by_symbol.get(symbol)
            if rules is None:
                return self._failure_result(
                    opportunity=opportunity,
                    failure=ExecutionFailure.CONVERSION_ERROR,
                    failure_leg_index=index,
                    completion_ms=book.received_time_ms,
                    amount=amount,
                    exposure_asset=assets[index],
                    executed=executed,
                )

            try:
                fill = self.converter.convert(
                    rules=rules,
                    book=book,
                    from_asset=assets[index],
                    to_asset=assets[index + 1],
                    input_amount=amount,
                )
            except ConversionError:
                return self._failure_result(
                    opportunity=opportunity,
                    failure=ExecutionFailure.CONVERSION_ERROR,
                    failure_leg_index=index,
                    completion_ms=book.received_time_ms,
                    amount=amount,
                    exposure_asset=assets[index],
                    executed=executed,
                )

            executed.append(
                ExecutedLeg(
                    index=index,
                    scheduled_ms=scheduled_ms,
                    book_received_ms=book.received_time_ms,
                    wait_ms=max(0, book.received_time_ms - scheduled_ms),
                    fill=fill,
                )
            )
            amount = fill.net_output
            completion_ms = book.received_time_ms

        profit = amount - opportunity.starting_amount
        realized_edge = profit / opportunity.starting_amount * TEN_THOUSAND
        return SequentialFillResult(
            route_id=route.route_id,
            signal_time_ms=opportunity.created_time_ms,
            profile_name=self.latency.name,
            completed=True,
            failure=ExecutionFailure.NONE,
            failure_leg_index=None,
            starting_amount=opportunity.starting_amount,
            final_amount=amount,
            net_profit=profit,
            detected_edge_bps=opportunity.net_edge_bps,
            realized_edge_bps=realized_edge,
            edge_decay_bps=opportunity.net_edge_bps - realized_edge,
            completion_ms=completion_ms,
            exposure_asset=None,
            exposure_amount=ZERO,
            legs=tuple(executed),
        )

    def _failure_result(
        self,
        *,
        opportunity: TriangularOpportunity,
        failure: ExecutionFailure,
        failure_leg_index: int,
        completion_ms: int,
        amount: Decimal,
        exposure_asset: str,
        executed: list[ExecutedLeg],
    ) -> SequentialFillResult:
        return SequentialFillResult(
            route_id=opportunity.route.route_id,
            signal_time_ms=opportunity.created_time_ms,
            profile_name=self.latency.name,
            completed=False,
            failure=failure,
            failure_leg_index=failure_leg_index,
            starting_amount=opportunity.starting_amount,
            final_amount=ZERO,
            net_profit=ZERO,
            detected_edge_bps=opportunity.net_edge_bps,
            realized_edge_bps=ZERO,
            edge_decay_bps=opportunity.net_edge_bps,
            completion_ms=completion_ms,
            exposure_asset=exposure_asset,
            exposure_amount=amount,
            legs=tuple(executed),
        )
