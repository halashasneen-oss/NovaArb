from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from novaarb.candidate_bus import (
    CandidateEnvelope,
    cross_venue_envelope,
    funding_envelope,
)
from novaarb.funding import FundingCarryScanner, FundingScannerEvent
from novaarb.research import ResearchRecorder
from novaarb.shadow_scanner import MultiInstrumentPublicShadowScanner, ShadowScannerEvent
from novaarb.unified_shadow import UnifiedShadowCoordinator


@dataclass(frozen=True, slots=True)
class UnifiedShadowLiveConfig:
    allocation_window_ms: int = 100
    execution_delay_ms: int = 50
    heartbeat_interval_ms: int = 5_000
    max_data_staleness_ms: int = 2_000
    funding_capital_multiplier: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        if min(
            self.allocation_window_ms,
            self.execution_delay_ms,
            self.max_data_staleness_ms,
        ) < 0:
            raise ValueError("unified shadow timing values cannot be negative")
        if self.heartbeat_interval_ms <= 0:
            raise ValueError("heartbeat_interval_ms must be positive")
        if self.funding_capital_multiplier <= 0:
            raise ValueError("funding_capital_multiplier must be positive")


def sync_funding_scanner_state(
    scanner: FundingCarryScanner,
    coordinator: UnifiedShadowCoordinator,
) -> None:
    """Copy public funding/books into the stateful portfolio without exchange actions."""

    for snapshot in scanner.funding.values():
        if snapshot.symbol.upper() in coordinator.funding_assets:
            coordinator.observe_funding(snapshot)
    for snapshot in scanner.books.values():
        if snapshot.symbol.upper() in coordinator.funding_assets:
            coordinator.observe_funding_book(snapshot)


def _cross_envelope(event: ShadowScannerEvent, *, sequence: int) -> CandidateEnvelope:
    return cross_venue_envelope(event.opportunity, sequence=sequence)


def _funding_envelope(
    event: FundingScannerEvent,
    *,
    coordinator: UnifiedShadowCoordinator,
    sequence: int,
    capital_multiplier: Decimal,
) -> CandidateEnvelope:
    mapping = coordinator.funding_assets.get(event.opportunity.symbol.upper())
    if mapping is None:
        raise ValueError(f"missing funding asset mapping for {event.opportunity.symbol}")
    return funding_envelope(
        event.opportunity,
        base_asset=mapping.base_asset,
        quote_asset=mapping.quote_asset,
        sequence=sequence,
        venue=coordinator.funding_book.config.venue,
        capital_multiplier=capital_multiplier,
    )


def _data_healthy(
    coordinator: UnifiedShadowCoordinator,
    *,
    now_ms: int,
    max_staleness_ms: int,
) -> bool:
    latest_by_venue: dict[str, int] = {}
    for book in coordinator.engine.books.values():
        latest_by_venue[book.venue] = max(
            latest_by_venue.get(book.venue, 0),
            book.snapshot.received_time_ms,
        )
    if not all(
        venue in latest_by_venue
        and now_ms - latest_by_venue[venue] <= max_staleness_ms
        for venue in coordinator.engine.cost_venues
    ):
        return False

    for position in coordinator.funding_book.positions.values():
        spot = coordinator.funding_books.get(
            (position.symbol.upper(), position_market("spot"))
        )
        perpetual = coordinator.funding_books.get(
            (position.symbol.upper(), position_market("perpetual"))
        )
        if spot is None or perpetual is None:
            return False
        if max(spot.age_ms(now_ms), perpetual.age_ms(now_ms)) > max_staleness_ms:
            return False
    return True


def position_market(value: Literal["spot", "perpetual"]):
    from novaarb.domain import MarketType

    return MarketType(value)


def unified_metrics(
    coordinator: UnifiedShadowCoordinator,
    *,
    now_ms: int | None = None,
    max_data_staleness_ms: int = 2_000,
) -> dict[str, object]:
    timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
    mark = coordinator.portfolio_mark()
    return {
        "timestamp_ms": timestamp_ms,
        "mode": "unified_shadow_public_data_only",
        "quote_asset": coordinator.quote_asset,
        "data_healthy": _data_healthy(
            coordinator,
            now_ms=timestamp_ms,
            max_staleness_ms=max_data_staleness_ms,
        ),
        "realized_pnl_quote": coordinator.realized_pnl_quote,
        "cross_venue_realized_pnl_quote": coordinator.cross_venue_realized_pnl,
        "funding_realized_pnl_quote": coordinator.funding_realized_pnl,
        "cross_venue_fills": len(coordinator.cross_venue_fills),
        "funding_open_positions": coordinator.funding_book.open_positions,
        "funding_reserved_capital_quote": coordinator.funding_book.reserved_capital_quote,
        "funding_closes": len(coordinator.funding_closes),
        "decayed": coordinator.decayed,
        "inventory_rejections": coordinator.inventory_rejections,
        "risk_halts": coordinator.risk_halts,
        "unsupported_selected": coordinator.unsupported_selected,
        "allocation_rejection_reasons": dict(
            sorted(coordinator.allocation_reasons.items())
        ),
        "kill_switch_reasons": dict(sorted(coordinator.kill_reasons.items())),
        "portfolio_mark": asdict(mark) if mark is not None else None,
        "balances": [asdict(item) for item in coordinator.inventory.snapshot()],
    }


async def run_unified_public_shadow_session(
    *,
    cross_scanner: MultiInstrumentPublicShadowScanner,
    funding_scanner: FundingCarryScanner,
    coordinator: UnifiedShadowCoordinator,
    metrics_recorder: ResearchRecorder | None = None,
    duration_seconds: float | None = None,
    config: UnifiedShadowLiveConfig | None = None,
) -> None:
    """Run a mixed cross-venue/funding shadow session using only public market data."""

    if duration_seconds is not None and duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive when provided")
    resolved = config or UnifiedShadowLiveConfig()
    cross_queue = await cross_scanner.events()
    funding_queue = await funding_scanner.events()
    sequence = 0
    loop = asyncio.get_running_loop()
    started = loop.time()
    next_heartbeat = started

    tasks: dict[asyncio.Task[object], str] = {
        asyncio.create_task(cross_queue.get()): "cross",
        asyncio.create_task(funding_queue.get()): "funding",
    }

    def restart(source: str) -> None:
        queue = cross_queue if source == "cross" else funding_queue
        tasks[asyncio.create_task(queue.get())] = source

    def collect_envelope(source: str, event: object) -> CandidateEnvelope:
        nonlocal sequence
        sequence += 1
        if source == "cross":
            if not isinstance(event, ShadowScannerEvent):
                raise TypeError("cross shadow queue emitted an unexpected event")
            return _cross_envelope(event, sequence=sequence)
        if not isinstance(event, FundingScannerEvent):
            raise TypeError("funding shadow queue emitted an unexpected event")
        return _funding_envelope(
            event,
            coordinator=coordinator,
            sequence=sequence,
            capital_multiplier=resolved.funding_capital_multiplier,
        )

    def maintain_funding(now_ms: int) -> None:
        sync_funding_scanner_state(funding_scanner, coordinator)
        coordinator.settle_funding(now_ms=now_ms)
        coordinator.close_ready_funding(now_ms=now_ms)

    try:
        while True:
            now = loop.time()
            if duration_seconds is not None and now - started >= duration_seconds:
                break
            heartbeat_timeout = max(0.0, next_heartbeat - now)
            duration_timeout = (
                max(0.0, duration_seconds - (now - started))
                if duration_seconds is not None
                else None
            )
            timeout = heartbeat_timeout
            if duration_timeout is not None:
                timeout = min(timeout, duration_timeout)

            if timeout <= 0:
                timestamp_ms = int(time.time() * 1000)
                maintain_funding(timestamp_ms)
                if metrics_recorder is not None:
                    metrics_recorder.append_metadata(
                        "unified_shadow_heartbeat",
                        unified_metrics(
                            coordinator,
                            now_ms=timestamp_ms,
                            max_data_staleness_ms=resolved.max_data_staleness_ms,
                        ),
                    )
                next_heartbeat = loop.time() + resolved.heartbeat_interval_ms / 1000
                continue

            done, _ = await asyncio.wait(
                tuple(tasks),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                continue

            envelopes: list[CandidateEnvelope] = []
            for task in done:
                source = tasks.pop(task)
                event = task.result()
                restart(source)
                envelopes.append(collect_envelope(source, event))

            deadline = loop.time() + resolved.allocation_window_ms / 1000
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                more, _ = await asyncio.wait(
                    tuple(tasks),
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not more:
                    break
                for task in more:
                    source = tasks.pop(task)
                    event = task.result()
                    restart(source)
                    envelopes.append(collect_envelope(source, event))

            sync_funding_scanner_state(funding_scanner, coordinator)
            if resolved.execution_delay_ms:
                await asyncio.sleep(resolved.execution_delay_ms / 1000)
            timestamp_ms = int(time.time() * 1000)
            coordinator.process_batch(
                tuple(envelopes),
                now_ms=timestamp_ms,
                data_healthy=_data_healthy(
                    coordinator,
                    now_ms=timestamp_ms,
                    max_staleness_ms=resolved.max_data_staleness_ms,
                ),
            )
            maintain_funding(timestamp_ms)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    if metrics_recorder is not None:
        timestamp_ms = int(time.time() * 1000)
        metrics_recorder.append_metadata(
            "unified_shadow_final",
            unified_metrics(
                coordinator,
                now_ms=timestamp_ms,
                max_data_staleness_ms=resolved.max_data_staleness_ms,
            ),
        )
