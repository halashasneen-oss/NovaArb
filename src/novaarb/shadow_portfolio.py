from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from novaarb.allocator import AllocationConfig, CapitalAwareAllocator, ResearchCandidate
from novaarb.cross_venue import CrossVenueConfig, CrossVenueOpportunity, VenueCostProfile
from novaarb.cross_venue_execution import reprice_committed_cross_venue
from novaarb.domain import MarketType, TEN_THOUSAND, ZERO
from novaarb.inventory import ExecutedCrossVenueTrade
from novaarb.research import iter_records, snapshot_from_record
from novaarb.shadow import (
    AssetBalance,
    MultiAssetInventoryLedger,
    PortfolioMark,
    ShadowKillSwitchReason,
    ShadowRiskConfig,
    ShadowRiskGuard,
)
from novaarb.shadow_scanner import MultiInstrumentShadowEngine, ShadowScannerEvent
from novaarb.venue import Instrument, NormalizedBook, VenueInstrument


@dataclass(frozen=True, slots=True)
class ShadowPortfolioReplayConfig:
    allocation_window_ms: int = 100
    signal_cooldown_ms: int = 1_000
    max_data_staleness_ms: int = 2_000

    def __post_init__(self) -> None:
        if min(
            self.allocation_window_ms,
            self.signal_cooldown_ms,
            self.max_data_staleness_ms,
        ) < 0:
            raise ValueError("shadow replay timing values cannot be negative")


@dataclass(frozen=True, slots=True)
class ShadowPortfolioTrade:
    timestamp_ms: int
    instrument: str
    base_asset: str
    quote_asset: str
    buy_venue: str
    sell_venue: str
    detected_edge_bps: Decimal
    realized_edge_bps: Decimal
    edge_decay_bps: Decimal
    conservative_net_profit_quote: Decimal
    capital_required_quote: Decimal


@dataclass(frozen=True, slots=True)
class ShadowSymbolSummary:
    instrument: str
    detected_signals: int
    allocator_selected: int
    executed_trades: int
    conservative_net_profit_quote: Decimal
    median_realized_edge_bps: Decimal


@dataclass(frozen=True, slots=True)
class ShadowPortfolioSummary:
    snapshots: int
    detected_signals: int
    allocator_selected: int
    allocator_rejections: int
    decayed_before_execution: int
    inventory_rejections: int
    risk_halts: int
    executed_trades: int
    profitable_trades: int
    conservative_net_profit_quote: Decimal
    max_drawdown_quote: Decimal
    allocation_rejection_reasons: dict[str, int]
    kill_switch_reasons: dict[str, int]
    ending_balances: tuple[AssetBalance, ...]
    ending_mark: PortfolioMark
    symbols: tuple[ShadowSymbolSummary, ...]
    trades: tuple[ShadowPortfolioTrade, ...]


@dataclass(frozen=True, slots=True)
class _PendingSignal:
    event: ShadowScannerEvent
    candidate: ResearchCandidate


@dataclass(slots=True)
class _MutableSymbolStats:
    detected: int = 0
    selected: int = 0
    executed: int = 0
    profit: Decimal = ZERO
    realized_edges: list[Decimal] | None = None

    def __post_init__(self) -> None:
        if self.realized_edges is None:
            self.realized_edges = []


def _decimal_median(values: list[Decimal]) -> Decimal:
    return Decimal(median(values)) if values else ZERO


def _mapping_key(venue: str, symbol: str, market: MarketType) -> tuple[str, str, MarketType]:
    return (venue.lower(), symbol.upper(), market)


def _build_instrument_map(
    instruments: tuple[VenueInstrument, ...],
    *,
    quote_asset: str,
) -> dict[tuple[str, str, MarketType], Instrument]:
    if not instruments:
        raise ValueError("at least one venue instrument is required")
    mapping: dict[tuple[str, str, MarketType], Instrument] = {}
    canonical_venues: dict[str, set[str]] = {}
    for item in instruments:
        if item.instrument.market is not MarketType.SPOT:
            raise ValueError("shadow portfolio replay currently supports Spot instruments only")
        if item.instrument.quote_asset != quote_asset:
            raise ValueError("all shadow replay instruments must share the portfolio quote asset")
        key = _mapping_key(item.venue, item.venue_symbol, item.instrument.market)
        if key in mapping:
            raise ValueError(f"duplicate replay instrument mapping for {key}")
        mapping[key] = item.instrument
        canonical_venues.setdefault(item.instrument.canonical_symbol, set()).add(
            item.venue.lower()
        )
    if any(len(venues) < 2 for venues in canonical_venues.values()):
        raise ValueError("every replay instrument must map to at least two venues")
    return mapping


def _cost_map(costs: tuple[VenueCostProfile, ...]) -> dict[str, VenueCostProfile]:
    by_venue = {item.venue: item for item in costs}
    if len(by_venue) != len(costs):
        raise ValueError("venue cost profiles must be unique")
    if len(by_venue) < 2:
        raise ValueError("shadow portfolio replay requires at least two venues")
    return by_venue


def _fee_for_opportunity(
    opportunity: CrossVenueOpportunity,
    *,
    venue: str,
    costs: dict[str, VenueCostProfile],
) -> Decimal:
    profile = costs[venue]
    if venue == opportunity.buy_venue:
        notional = opportunity.buy_quote_required
    elif venue == opportunity.sell_venue:
        notional = opportunity.sell_quote_proceeds
    else:
        raise ValueError("venue is not part of opportunity")
    return notional * profile.taker_fee_bps / TEN_THOUSAND


def _capital_required(opportunity: CrossVenueOpportunity) -> Decimal:
    return opportunity.buy_quote_required + opportunity.sell_quote_proceeds


def _candidate_from_event(
    event: ShadowScannerEvent,
    *,
    sequence: int,
) -> ResearchCandidate:
    opportunity = event.opportunity
    instrument = opportunity.instrument
    return ResearchCandidate(
        opportunity_id=(
            f"{opportunity.created_time_ms}:{sequence}:"
            f"{instrument.canonical_symbol}:{opportunity.buy_venue}:{opportunity.sell_venue}"
        ),
        strategy="prefunded_cross_venue",
        capital_required_usdt=_capital_required(opportunity),
        expected_net_profit_usdt=opportunity.net_profit_quote,
        expected_edge_bps=opportunity.net_edge_bps,
        resource_keys=(
            f"instrument:{instrument.canonical_symbol}",
            f"route:{opportunity.buy_venue}:{opportunity.sell_venue}:"
            f"{instrument.canonical_symbol}",
        ),
        observed_at_ms=opportunity.created_time_ms,
    )


def _current_prices(
    latest_books: dict[tuple[str, str], NormalizedBook],
    *,
    quote_asset: str,
) -> dict[str, Decimal]:
    prices: dict[str, list[Decimal]] = {}
    for book in latest_books.values():
        instrument = book.instrument
        if instrument.market is not MarketType.SPOT:
            continue
        if instrument.quote_asset != quote_asset:
            continue
        prices.setdefault(instrument.base_asset, []).append(book.snapshot.mid_price)
    return {
        asset: Decimal(median(values))
        for asset, values in prices.items()
        if values
    }


def _required_mark_assets(
    balances: tuple[AssetBalance, ...],
    *,
    quote_asset: str,
) -> set[str]:
    return {
        item.asset
        for item in balances
        if item.asset != quote_asset and item.quantity > ZERO
    }


def _data_is_healthy(
    *,
    now_ms: int,
    venues: tuple[str, ...],
    latest_received_ms: dict[str, int],
    max_staleness_ms: int,
) -> bool:
    return all(
        venue in latest_received_ms
        and now_ms - latest_received_ms[venue] <= max_staleness_ms
        for venue in venues
    )


def replay_shadow_portfolio(
    path: str,
    *,
    instruments: tuple[VenueInstrument, ...],
    costs: tuple[VenueCostProfile, ...],
    balances: tuple[AssetBalance, ...],
    quote_asset: str,
    allocation_config: AllocationConfig,
    strategy_config: CrossVenueConfig | None = None,
    risk_config: ShadowRiskConfig | None = None,
    replay_config: ShadowPortfolioReplayConfig | None = None,
) -> ShadowPortfolioSummary:
    """Replay a multi-instrument public capture through one shared shadow portfolio."""

    quote = quote_asset.upper()
    resolved_strategy = strategy_config or CrossVenueConfig()
    resolved_risk = risk_config or ShadowRiskConfig()
    resolved_replay = replay_config or ShadowPortfolioReplayConfig()
    instrument_map = _build_instrument_map(instruments, quote_asset=quote)
    costs_by_venue = _cost_map(costs)
    venues = tuple(sorted(costs_by_venue))
    mapped_venues = {item.venue.lower() for item in instruments}
    if mapped_venues != set(venues):
        raise ValueError("replay instrument venues must match venue cost profiles")
    balance_venues = {item.venue for item in balances}
    if not set(venues) <= balance_venues:
        raise ValueError("every venue cost profile requires shadow inventory balances")

    normalized_books: list[NormalizedBook] = []
    for record in iter_records(path):
        if record.get("kind") != "book":
            continue
        snapshot = snapshot_from_record(record)
        key = _mapping_key(snapshot.venue, snapshot.symbol, snapshot.market)
        instrument = instrument_map.get(key)
        if instrument is None:
            continue
        normalized_books.append(
            NormalizedBook(
                instrument=instrument,
                venue_symbol=snapshot.symbol,
                snapshot=snapshot,
            )
        )
    if not normalized_books:
        raise ValueError("capture contains no mapped order books")
    normalized_books.sort(key=lambda item: item.snapshot.received_time_ms)

    inventory = MultiAssetInventoryLedger(balances)
    engine = MultiInstrumentShadowEngine(
        config=resolved_strategy,
        costs=costs,
        inventory=inventory,
    )
    allocator = CapitalAwareAllocator(allocation_config)
    guard = ShadowRiskGuard(resolved_risk)

    latest_received_ms: dict[str, int] = {}
    last_signal_ms: dict[tuple[str, str, str], int] = {}
    pending: list[_PendingSignal] = []
    window_started_ms: int | None = None
    sequence = 0
    snapshots = 0
    detected_signals = 0
    allocator_selected = 0
    allocator_rejections = 0
    decayed_before_execution = 0
    inventory_rejections = 0
    risk_halts = 0
    allocation_reasons: Counter[str] = Counter()
    kill_reasons: Counter[str] = Counter()
    executed: list[ShadowPortfolioTrade] = []
    symbol_stats: dict[str, _MutableSymbolStats] = {}
    total_profit = ZERO
    peak_value: Decimal | None = None
    max_drawdown = ZERO

    def mark_portfolio() -> PortfolioMark | None:
        prices = _current_prices(engine.books, quote_asset=quote)
        required = _required_mark_assets(inventory.snapshot(), quote_asset=quote)
        if not required <= set(prices):
            return None
        return inventory.mark_to_quote(
            quote_asset=quote,
            prices_in_quote=prices,
        )

    def update_drawdown() -> None:
        nonlocal max_drawdown
        nonlocal peak_value
        mark = mark_portfolio()
        if mark is None:
            return
        if peak_value is None:
            peak_value = mark.total_value_quote
        else:
            peak_value = max(peak_value, mark.total_value_quote)
        max_drawdown = max(max_drawdown, peak_value - mark.total_value_quote)

    def flush(now_ms: int) -> None:
        nonlocal allocator_rejections
        nonlocal allocator_selected
        nonlocal decayed_before_execution
        nonlocal inventory_rejections
        nonlocal risk_halts
        nonlocal total_profit
        if not pending:
            return

        mark = mark_portfolio()
        healthy = _data_is_healthy(
            now_ms=now_ms,
            venues=venues,
            latest_received_ms=latest_received_ms,
            max_staleness_ms=resolved_replay.max_data_staleness_ms,
        )
        if mark is None:
            risk_halts += len(pending)
            kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += len(pending)
            pending.clear()
            return

        pre_decision = guard.assess(
            timestamp_ms=now_ms,
            mark=mark,
            data_healthy=healthy,
        )
        if not pre_decision.allowed:
            risk_halts += len(pending)
            kill_reasons[pre_decision.reason.value] += len(pending)
            pending.clear()
            return

        result = allocator.allocate(tuple(item.candidate for item in pending))
        by_id = {item.candidate.opportunity_id: item for item in pending}
        for decision in result.decisions:
            if decision.selected:
                allocator_selected += 1
                instrument_key = decision.candidate.resource_keys[0].split(":", 1)[1]
                symbol_stats[instrument_key].selected += 1
            else:
                allocator_rejections += 1
                allocation_reasons[decision.reason.value] += 1

        for candidate in result.selected:
            item = by_id[candidate.opportunity_id]
            detected = item.event.opportunity
            instrument = detected.instrument
            buy_book = engine.books.get((instrument.canonical_symbol, detected.buy_venue))
            sell_book = engine.books.get((instrument.canonical_symbol, detected.sell_venue))
            if buy_book is None or sell_book is None:
                decayed_before_execution += 1
                continue

            realized = reprice_committed_cross_venue(
                detected,
                buy_book=buy_book,
                sell_book=sell_book,
                costs=costs,
                now_ms=now_ms,
            )
            if realized is None:
                decayed_before_execution += 1
                continue
            if (
                max(realized.buy_book_age_ms, realized.sell_book_age_ms)
                > resolved_strategy.max_book_age_ms
                or realized.book_skew_ms > resolved_strategy.max_book_skew_ms
            ):
                risk_halts += 1
                kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += 1
                continue

            current_mark = mark_portfolio()
            current_health = _data_is_healthy(
                now_ms=now_ms,
                venues=venues,
                latest_received_ms=latest_received_ms,
                max_staleness_ms=resolved_replay.max_data_staleness_ms,
            )
            if current_mark is None:
                risk_halts += 1
                kill_reasons[ShadowKillSwitchReason.DATA_UNHEALTHY.value] += 1
                continue
            risk = guard.assess(
                timestamp_ms=now_ms,
                mark=current_mark,
                data_healthy=current_health,
            )
            if not risk.allowed:
                risk_halts += 1
                kill_reasons[risk.reason.value] += 1
                continue

            buy_fee = _fee_for_opportunity(
                realized,
                venue=realized.buy_venue,
                costs=costs_by_venue,
            )
            sell_fee = _fee_for_opportunity(
                realized,
                venue=realized.sell_venue,
                costs=costs_by_venue,
            )
            trade = ExecutedCrossVenueTrade(
                buy_venue=realized.buy_venue,
                sell_venue=realized.sell_venue,
                base_quantity=realized.base_quantity,
                buy_quote_spent=realized.buy_quote_required,
                sell_quote_received=realized.sell_quote_proceeds,
                buy_fee_quote=buy_fee,
                sell_fee_quote=sell_fee,
            )
            try:
                engine.apply_shadow_fill(
                    trade,
                    base_asset=instrument.base_asset,
                    quote_asset=instrument.quote_asset,
                )
            except ValueError:
                inventory_rejections += 1
                continue

            conservative_profit = realized.net_profit_quote
            guard.record_realized_pnl(
                timestamp_ms=now_ms,
                pnl_quote=conservative_profit,
            )
            total_profit += conservative_profit
            edge_decay = detected.net_edge_bps - realized.net_edge_bps
            executed_trade = ShadowPortfolioTrade(
                timestamp_ms=now_ms,
                instrument=instrument.canonical_symbol,
                base_asset=instrument.base_asset,
                quote_asset=instrument.quote_asset,
                buy_venue=realized.buy_venue,
                sell_venue=realized.sell_venue,
                detected_edge_bps=detected.net_edge_bps,
                realized_edge_bps=realized.net_edge_bps,
                edge_decay_bps=edge_decay,
                conservative_net_profit_quote=conservative_profit,
                capital_required_quote=_capital_required(realized),
            )
            executed.append(executed_trade)
            stats = symbol_stats[instrument.canonical_symbol]
            stats.executed += 1
            stats.profit += conservative_profit
            assert stats.realized_edges is not None
            stats.realized_edges.append(realized.net_edge_bps)
            update_drawdown()

        pending.clear()

    for book in normalized_books:
        snapshots += 1
        now_ms = book.snapshot.received_time_ms
        latest_received_ms[book.venue] = now_ms
        events = engine.process_book(book, now_ms=now_ms)
        update_drawdown()

        if (
            window_started_ms is not None
            and now_ms - window_started_ms >= resolved_replay.allocation_window_ms
        ):
            flush(now_ms)
            window_started_ms = None

        for event in events:
            if not event.decision.approved:
                continue
            opportunity = event.opportunity
            route_key = (
                opportunity.instrument.canonical_symbol,
                opportunity.buy_venue,
                opportunity.sell_venue,
            )
            previous = last_signal_ms.get(route_key)
            if (
                previous is not None
                and now_ms - previous < resolved_replay.signal_cooldown_ms
            ):
                continue
            last_signal_ms[route_key] = now_ms
            detected_signals += 1
            stats = symbol_stats.setdefault(
                opportunity.instrument.canonical_symbol,
                _MutableSymbolStats(),
            )
            stats.detected += 1
            sequence += 1
            pending.append(
                _PendingSignal(
                    event=event,
                    candidate=_candidate_from_event(event, sequence=sequence),
                )
            )
            if window_started_ms is None:
                window_started_ms = now_ms

    final_time_ms = normalized_books[-1].snapshot.received_time_ms
    flush(final_time_ms)
    ending_mark = mark_portfolio()
    if ending_mark is None:
        raise ValueError("cannot mark ending shadow portfolio from captured instruments")
    update_drawdown()

    summaries = tuple(
        ShadowSymbolSummary(
            instrument=instrument,
            detected_signals=stats.detected,
            allocator_selected=stats.selected,
            executed_trades=stats.executed,
            conservative_net_profit_quote=stats.profit,
            median_realized_edge_bps=_decimal_median(stats.realized_edges or []),
        )
        for instrument, stats in sorted(symbol_stats.items())
    )
    profitable = sum(
        1 for trade in executed if trade.conservative_net_profit_quote > ZERO
    )
    return ShadowPortfolioSummary(
        snapshots=snapshots,
        detected_signals=detected_signals,
        allocator_selected=allocator_selected,
        allocator_rejections=allocator_rejections,
        decayed_before_execution=decayed_before_execution,
        inventory_rejections=inventory_rejections,
        risk_halts=risk_halts,
        executed_trades=len(executed),
        profitable_trades=profitable,
        conservative_net_profit_quote=total_profit,
        max_drawdown_quote=max_drawdown,
        allocation_rejection_reasons=dict(sorted(allocation_reasons.items())),
        kill_switch_reasons=dict(sorted(kill_reasons.items())),
        ending_balances=inventory.snapshot(),
        ending_mark=ending_mark,
        symbols=summaries,
        trades=tuple(executed),
    )
