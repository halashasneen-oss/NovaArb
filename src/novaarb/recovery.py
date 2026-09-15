from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from novaarb.domain import TEN_THOUSAND, ZERO
from novaarb.execution import BookTimeline, ExecutionFailure, SequentialFillResult
from novaarb.symbols import SymbolRules
from novaarb.triangular import ConversionError, ConversionFill, SpotConverter, TriangularOpportunity


class RecoveryFailure(StrEnum):
    NONE = "none"
    NOT_REQUIRED = "not_required"
    UNSUPPORTED_EXPOSURE = "unsupported_exposure"
    MISSING_RECOVERY_BOOK = "missing_recovery_book"
    CONVERSION_ERROR = "conversion_error"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    delay_ms: int = 50
    max_book_wait_ms: int = 250

    def __post_init__(self) -> None:
        if self.delay_ms < 0:
            raise ValueError("delay_ms cannot be negative")
        if self.max_book_wait_ms < 0:
            raise ValueError("max_book_wait_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    attempted: bool
    recovered: bool
    failure: RecoveryFailure
    scheduled_ms: int
    completion_ms: int
    exposure_asset: str | None
    anchor_amount: Decimal
    net_profit: Decimal
    realized_edge_bps: Decimal
    fill: ConversionFill | None


class EmergencyUnwinder:
    """Attempts a deterministic market unwind after a partially executed triangle."""

    def __init__(
        self,
        *,
        rules: tuple[SymbolRules, ...],
        taker_fee_bps: Decimal,
        policy: RecoveryPolicy = RecoveryPolicy(),
    ) -> None:
        self.rules_by_symbol = {rule.symbol: rule for rule in rules}
        self.converter = SpotConverter(taker_fee_bps=taker_fee_bps)
        self.policy = policy

    def recover(
        self,
        opportunity: TriangularOpportunity,
        failed: SequentialFillResult,
        timeline: BookTimeline,
    ) -> RecoveryResult:
        anchor = opportunity.route.anchor_asset
        if failed.completed:
            return RecoveryResult(
                attempted=False,
                recovered=True,
                failure=RecoveryFailure.NOT_REQUIRED,
                scheduled_ms=failed.completion_ms,
                completion_ms=failed.completion_ms,
                exposure_asset=None,
                anchor_amount=failed.final_amount,
                net_profit=failed.net_profit,
                realized_edge_bps=failed.realized_edge_bps,
                fill=None,
            )
        if not failed.legs or failed.exposure_asset == anchor:
            return RecoveryResult(
                attempted=False,
                recovered=True,
                failure=RecoveryFailure.NOT_REQUIRED,
                scheduled_ms=failed.completion_ms,
                completion_ms=failed.completion_ms,
                exposure_asset=anchor,
                anchor_amount=opportunity.starting_amount,
                net_profit=ZERO,
                realized_edge_bps=ZERO,
                fill=None,
            )

        exposure = failed.exposure_asset
        if exposure == opportunity.route.first_asset:
            symbol = opportunity.route.symbols[0]
        elif exposure == opportunity.route.second_asset:
            symbol = opportunity.route.symbols[2]
        else:
            return self._failure(
                failed=failed,
                failure=RecoveryFailure.UNSUPPORTED_EXPOSURE,
                scheduled_ms=failed.completion_ms + self.policy.delay_ms,
            )

        scheduled_ms = failed.completion_ms + self.policy.delay_ms
        book, book_failure = timeline.at_or_after(
            symbol,
            scheduled_ms,
            max_wait_ms=self.policy.max_book_wait_ms,
        )
        if book is None:
            failure = (
                RecoveryFailure.MISSING_RECOVERY_BOOK
                if book_failure
                in {ExecutionFailure.MISSING_FUTURE_BOOK, ExecutionFailure.BOOK_WAIT_EXCEEDED}
                else RecoveryFailure.CONVERSION_ERROR
            )
            return self._failure(
                failed=failed,
                failure=failure,
                scheduled_ms=scheduled_ms,
            )

        rules = self.rules_by_symbol.get(symbol)
        if rules is None:
            return self._failure(
                failed=failed,
                failure=RecoveryFailure.UNSUPPORTED_EXPOSURE,
                scheduled_ms=scheduled_ms,
            )
        try:
            fill = self.converter.convert(
                rules=rules,
                book=book,
                from_asset=exposure,
                to_asset=anchor,
                input_amount=failed.exposure_amount,
            )
        except ConversionError:
            return self._failure(
                failed=failed,
                failure=RecoveryFailure.CONVERSION_ERROR,
                scheduled_ms=scheduled_ms,
                completion_ms=book.received_time_ms,
            )

        anchor_amount = fill.net_output
        net_profit = anchor_amount - opportunity.starting_amount
        realized_edge = net_profit / opportunity.starting_amount * TEN_THOUSAND
        return RecoveryResult(
            attempted=True,
            recovered=True,
            failure=RecoveryFailure.NONE,
            scheduled_ms=scheduled_ms,
            completion_ms=book.received_time_ms,
            exposure_asset=exposure,
            anchor_amount=anchor_amount,
            net_profit=net_profit,
            realized_edge_bps=realized_edge,
            fill=fill,
        )

    @staticmethod
    def _failure(
        *,
        failed: SequentialFillResult,
        failure: RecoveryFailure,
        scheduled_ms: int,
        completion_ms: int | None = None,
    ) -> RecoveryResult:
        return RecoveryResult(
            attempted=True,
            recovered=False,
            failure=failure,
            scheduled_ms=scheduled_ms,
            completion_ms=completion_ms if completion_ms is not None else scheduled_ms,
            exposure_asset=failed.exposure_asset,
            anchor_amount=ZERO,
            net_profit=ZERO,
            realized_edge_bps=ZERO,
            fill=None,
        )
