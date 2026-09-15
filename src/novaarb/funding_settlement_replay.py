from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from statistics import median

from novaarb.domain import MarketType, OrderBookSnapshot, Side, TEN_THOUSAND, ZERO
from novaarb.funding import FundingCarryOpportunity, FundingCarryScanner, FundingSnapshot
from novaarb.funding_replay import funding_from_record, load_funding_session
from novaarb.orderbook import InsufficientLiquidity, simulate_base_fill
from novaarb.research import iter_records, snapshot_from_record


class FundingSettlementReason(StrEnum):
    MISSING_SETTLEMENT_RATE = "missing_settlement_rate"
    MISSING_NEXT_SETTLEMENT = "missing_next_settlement"
    MISSING_EXIT_BOOK = "missing_exit_book"
    EXIT_BOOK_SKEW = "exit_book_skew"
    EXIT_LIQUIDITY = "exit_liquidity"
    INVALID_SETTLEMENT_SCHEDULE = "invalid_settlement_schedule"


@dataclass(frozen=True, slots=True)
class FundingSettlementReplayConfig:
    max_exit_wait_ms: int = 60_000
    max_exit_book_skew_ms: int = 1_000
    entry_cooldown_ms: int = 5_000

    def __post_init__(self) -> None:
        if min(
            self.max_exit_wait_ms,
            self.max_exit_book_skew_ms,
            self.entry_cooldown_ms,
        ) < 0:
            raise ValueError("funding settlement replay timing values cannot be negative")


@dataclass(frozen=True, slots=True)
class FundingSettlementPayment:
    scheduled_time_ms: int
    observed_rate_bps: Decimal
    mark_price: Decimal
    payment_quote: Decimal


@dataclass(frozen=True, slots=True)
class FundingRealizedTrade:
    symbol: str
    entry_time_ms: int
    exit_time_ms: int
    base_quantity: Decimal
    spot_entry_price: Decimal
    spot_exit_price: Decimal
    futures_entry_price: Decimal
    futures_exit_price: Decimal
    settlement_count: int
    funding_quote: Decimal
    spot_pnl_quote: Decimal
    futures_pnl_quote: Decimal
    entry_fees_quote: Decimal
    exit_fees_quote: Decimal
    net_profit_quote: Decimal
    net_edge_bps: Decimal
    holding_ms: int
    settlements: tuple[FundingSettlementPayment, ...]


@dataclass(frozen=True, slots=True)
class FundingSettlementReplaySummary:
    candidate_entries: int
    attempted_positions: int
    completed_trades: int
    profitable_trades: int
    profitable_trade_rate: Decimal
    total_net_profit_quote: Decimal
    median_net_edge_bps: Decimal
    median_holding_ms: Decimal
    incomplete_reasons: dict[str, int]
    trades: tuple[FundingRealizedTrade, ...]


@dataclass(frozen=True, slots=True)
class _EntryCandidate:
    opportunity: FundingCarryOpportunity
    funding: FundingSnapshot


class _BookIndex:
    def __init__(self, books: tuple[OrderBookSnapshot, ...]) -> None:
        self.books = tuple(sorted(books, key=lambda item: item.received_time_ms))
        self.timestamps = [book.received_time_ms for book in self.books]

    def start_index(self, target_ms: int) -> int:
        return bisect_left(self.timestamps, target_ms)


def _synchronized_exit_books(
    spot_index: _BookIndex,
    futures_index: _BookIndex,
    *,
    target_ms: int,
    max_wait_ms: int,
    max_skew_ms: int,
) -> tuple[OrderBookSnapshot, OrderBookSnapshot] | FundingSettlementReason:
    spot_position = spot_index.start_index(target_ms)
    futures_position = futures_index.start_index(target_ms)

    while spot_position < len(spot_index.books) and futures_position < len(futures_index.books):
        spot = spot_index.books[spot_position]
        futures = futures_index.books[futures_position]
        if (
            spot.received_time_ms - target_ms > max_wait_ms
            or futures.received_time_ms - target_ms > max_wait_ms
        ):
            return FundingSettlementReason.MISSING_EXIT_BOOK

        skew = spot.received_time_ms - futures.received_time_ms
        if abs(skew) <= max_skew_ms:
            return spot, futures
        if skew < 0:
            spot_position += 1
        else:
            futures_position += 1

    return FundingSettlementReason.MISSING_EXIT_BOOK


def _latest_rate_for_target(
    snapshots: tuple[FundingSnapshot, ...],
    *,
    target_ms: int,
) -> FundingSnapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.next_funding_time_ms == target_ms
        and snapshot.received_time_ms <= target_ms
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.received_time_ms)


def _next_target_after(
    snapshots: tuple[FundingSnapshot, ...],
    *,
    previous_target_ms: int,
) -> int | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.received_time_ms > previous_target_ms
        and snapshot.next_funding_time_ms > previous_target_ms
    ]
    if not candidates:
        return None
    first = min(candidates, key=lambda item: item.received_time_ms)
    return first.next_funding_time_ms


def _build_settlements(
    candidate: _EntryCandidate,
    funding_snapshots: tuple[FundingSnapshot, ...],
) -> tuple[tuple[FundingSettlementPayment, ...], int] | FundingSettlementReason:
    opportunity = candidate.opportunity
    target_ms = candidate.funding.next_funding_time_ms
    if target_ms <= opportunity.created_time_ms:
        return FundingSettlementReason.INVALID_SETTLEMENT_SCHEDULE

    settlements: list[FundingSettlementPayment] = []
    for interval in range(opportunity.strategy_config.funding_intervals if False else 0):
        raise AssertionError(interval)

    return tuple(settlements), target_ms


def _settlement_schedule(
    candidate: _EntryCandidate,
    funding_snapshots: tuple[FundingSnapshot, ...],
    *,
    intervals: int,
) -> tuple[tuple[FundingSettlementPayment, ...], int] | FundingSettlementReason:
    opportunity = candidate.opportunity
    target_ms = candidate.funding.next_funding_time_ms
    if target_ms <= opportunity.created_time_ms:
        return FundingSettlementReason.INVALID_SETTLEMENT_SCHEDULE

    settlements: list[FundingSettlementPayment] = []
    for index in range(intervals):
        rate = _latest_rate_for_target(funding_snapshots, target_ms=target_ms)
        if rate is None:
            return FundingSettlementReason.MISSING_SETTLEMENT_RATE
        payment = opportunity.base_quantity * rate.mark_price * rate.funding_rate
        settlements.append(
            FundingSettlementPayment(
                scheduled_time_ms=target_ms,
                observed_rate_bps=rate.funding_rate_bps,
                mark_price=rate.mark_price,
                payment_quote=payment,
            )
        )
        if index + 1 < intervals:
            next_target = _next_target_after(
                funding_snapshots,
                previous_target_ms=target_ms,
            )
            if next_target is None or next_target <= target_ms:
                return FundingSettlementReason.MISSING_NEXT_SETTLEMENT
            target_ms = next_target
    return tuple(settlements), target_ms


def _realize_candidate(
    candidate: _EntryCandidate,
    *,
    funding_snapshots: tuple[FundingSnapshot, ...],
    spot_books: tuple[OrderBookSnapshot, ...],
    futures_books: tuple[OrderBookSnapshot, ...],
    spot_fee_bps: Decimal,
    futures_fee_bps: Decimal,
    intervals: int,
    replay_config: FundingSettlementReplayConfig,
) -> FundingRealizedTrade | FundingSettlementReason:
    schedule = _settlement_schedule(
        candidate,
        funding_snapshots,
        intervals=intervals,
    )
    if isinstance(schedule, FundingSettlementReason):
        return schedule
    settlements, exit_target_ms = schedule

    exit_books = _synchronized_exit_books(
        _BookIndex(spot_books),
        _BookIndex(futures_books),
        target_ms=exit_target_ms,
        max_wait_ms=replay_config.max_exit_wait_ms,
        max_skew_ms=replay_config.max_exit_book_skew_ms,
    )
    if isinstance(exit_books, FundingSettlementReason):
        return exit_books
    spot_exit_book, futures_exit_book = exit_books
    if (
        abs(spot_exit_book.received_time_ms - futures_exit_book.received_time_ms)
        > replay_config.max_exit_book_skew_ms
    ):
        return FundingSettlementReason.EXIT_BOOK_SKEW

    quantity = candidate.opportunity.base_quantity
    try:
        spot_exit = simulate_base_fill(spot_exit_book, Side.SELL, quantity)
        futures_exit = simulate_base_fill(futures_exit_book, Side.BUY, quantity)
    except InsufficientLiquidity:
        return FundingSettlementReason.EXIT_LIQUIDITY

    opportunity = candidate.opportunity
    spot_entry_quote = quantity * opportunity.spot_entry_price
    futures_entry_quote = quantity * opportunity.futures_entry_price
    spot_pnl = spot_exit.quote_quantity - spot_entry_quote
    futures_pnl = futures_entry_quote - futures_exit.quote_quantity
    funding_quote = sum((item.payment_quote for item in settlements), ZERO)
    exit_fees = (
        spot_exit.quote_quantity * spot_fee_bps / TEN_THOUSAND
        + futures_exit.quote_quantity * futures_fee_bps / TEN_THOUSAND
    )
    entry_fees = opportunity.entry_fees_usdt
    net = spot_pnl + futures_pnl + funding_quote - entry_fees - exit_fees
    reference_notional = opportunity.reference_notional_usdt
    net_edge = net / reference_notional * TEN_THOUSAND
    exit_time_ms = max(
        spot_exit_book.received_time_ms,
        futures_exit_book.received_time_ms,
    )
    return FundingRealizedTrade(
        symbol=opportunity.symbol,
        entry_time_ms=opportunity.created_time_ms,
        exit_time_ms=exit_time_ms,
        base_quantity=quantity,
        spot_entry_price=opportunity.spot_entry_price,
        spot_exit_price=spot_exit.average_price,
        futures_entry_price=opportunity.futures_entry_price,
        futures_exit_price=futures_exit.average_price,
        settlement_count=len(settlements),
        funding_quote=funding_quote,
        spot_pnl_quote=spot_pnl,
        futures_pnl_quote=futures_pnl,
        entry_fees_quote=entry_fees,
        exit_fees_quote=exit_fees,
        net_profit_quote=net,
        net_edge_bps=net_edge,
        holding_ms=max(0, exit_time_ms - opportunity.created_time_ms),
        settlements=settlements,
    )


def replay_funding_settlements(
    path: str,
    *,
    replay_config: FundingSettlementReplayConfig | None = None,
) -> FundingSettlementReplaySummary:
    """Replay approved funding carry entries through observed settlements and market exits.

    Funding payments use the latest captured public funding snapshot whose next-funding timestamp
    matches the scheduled settlement. This is deterministic modeled-realized research evidence,
    not an exchange account statement.
    """

    replay_config = replay_config or FundingSettlementReplayConfig()
    records = list(iter_records(path))
    symbols, config = load_funding_session(records)
    scanner = FundingCarryScanner(symbols=symbols, config=config)

    funding_by_symbol: dict[str, list[FundingSnapshot]] = defaultdict(list)
    books_by_key: dict[tuple[str, MarketType], list[OrderBookSnapshot]] = defaultdict(list)
    candidates: list[_EntryCandidate] = []

    for record in records:
        kind = record.get("kind")
        if kind == "funding":
            funding = funding_from_record(record)
            funding_by_symbol[funding.symbol].append(funding)
            scanner.update_funding((funding,))
            continue
        if kind != "book":
            continue
        book = snapshot_from_record(record)
        books_by_key[(book.symbol, book.market)].append(book)
        event = scanner.process_snapshot(book, now_ms=book.received_time_ms)
        if event is None or not event.decision.approved:
            continue
        funding = scanner.funding.get(event.opportunity.symbol)
        if funding is None:
            continue
        candidates.append(_EntryCandidate(event.opportunity, funding))

    for snapshots in funding_by_symbol.values():
        snapshots.sort(key=lambda item: item.received_time_ms)
    for books in books_by_key.values():
        books.sort(key=lambda item: item.received_time_ms)

    grouped_candidates: dict[str, list[_EntryCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped_candidates[candidate.opportunity.symbol].append(candidate)
    for symbol_candidates in grouped_candidates.values():
        symbol_candidates.sort(key=lambda item: item.opportunity.created_time_ms)

    incomplete: Counter[str] = Counter()
    completed: list[FundingRealizedTrade] = []
    attempted = 0

    for symbol in sorted(grouped_candidates):
        next_entry_ms = 0
        for candidate in grouped_candidates[symbol]:
            entry_ms = candidate.opportunity.created_time_ms
            if entry_ms < next_entry_ms:
                continue
            attempted += 1
            result = _realize_candidate(
                candidate,
                funding_snapshots=tuple(funding_by_symbol.get(symbol, ())),
                spot_books=tuple(books_by_key.get((symbol, MarketType.SPOT), ())),
                futures_books=tuple(books_by_key.get((symbol, MarketType.PERPETUAL), ())),
                spot_fee_bps=config.spot_taker_fee_bps,
                futures_fee_bps=config.futures_taker_fee_bps,
                intervals=config.funding_intervals,
                replay_config=replay_config,
            )
            if isinstance(result, FundingSettlementReason):
                incomplete[result.value] += 1
                next_entry_ms = entry_ms + replay_config.entry_cooldown_ms
                continue
            completed.append(result)
            next_entry_ms = result.exit_time_ms + replay_config.entry_cooldown_ms

    profits = [trade.net_profit_quote for trade in completed]
    edges = [trade.net_edge_bps for trade in completed]
    holdings = [trade.holding_ms for trade in completed]
    profitable = sum(1 for profit in profits if profit > ZERO)
    completed_count = len(completed)
    profitable_rate = (
        Decimal(profitable) / Decimal(completed_count)
        if completed_count
        else ZERO
    )
    return FundingSettlementReplaySummary(
        candidate_entries=len(candidates),
        attempted_positions=attempted,
        completed_trades=completed_count,
        profitable_trades=profitable,
        profitable_trade_rate=profitable_rate,
        total_net_profit_quote=sum(profits, ZERO),
        median_net_edge_bps=Decimal(str(median(edges))) if edges else ZERO,
        median_holding_ms=Decimal(str(median(holdings))) if holdings else ZERO,
        incomplete_reasons=dict(sorted(incomplete.items())),
        trades=tuple(completed),
    )
