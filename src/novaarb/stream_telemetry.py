from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novaarb.domain import MarketType


@dataclass(frozen=True, slots=True)
class StreamTelemetrySnapshot:
    venue: str
    market: MarketType
    connection_attempts: int
    successful_connections: int
    disconnects: int
    received_messages: int
    emitted_snapshots: int
    parse_errors: int
    rate_limit_events: int
    queue_drops: int
    cumulative_backoff_ms: int
    last_connected_ms: int | None
    last_message_ms: int | None
    last_snapshot_ms: int | None
    last_disconnect_ms: int | None


@dataclass(slots=True)
class _MutableStreamTelemetry:
    connection_attempts: int = 0
    successful_connections: int = 0
    disconnects: int = 0
    received_messages: int = 0
    emitted_snapshots: int = 0
    parse_errors: int = 0
    rate_limit_events: int = 0
    queue_drops: int = 0
    cumulative_backoff_ms: int = 0
    last_connected_ms: int | None = None
    last_message_ms: int | None = None
    last_snapshot_ms: int | None = None
    last_disconnect_ms: int | None = None


class StreamTelemetry:
    """In-process counters for public WebSocket reliability evidence.

    The collector never changes exchange behavior and never submits authenticated requests. It is
    intentionally small enough to share across adapter tasks in one asyncio event loop.
    """

    def __init__(self) -> None:
        self._streams: dict[tuple[str, MarketType], _MutableStreamTelemetry] = {}

    def _state(self, venue: str, market: MarketType) -> _MutableStreamTelemetry:
        key = (venue.lower(), market)
        state = self._streams.get(key)
        if state is None:
            state = _MutableStreamTelemetry()
            self._streams[key] = state
        return state

    def record_connection_attempt(self, venue: str, market: MarketType) -> None:
        self._state(venue, market).connection_attempts += 1

    def record_connected(self, venue: str, market: MarketType, *, timestamp_ms: int) -> None:
        state = self._state(venue, market)
        state.successful_connections += 1
        state.last_connected_ms = timestamp_ms

    def record_disconnect(self, venue: str, market: MarketType, *, timestamp_ms: int) -> None:
        state = self._state(venue, market)
        state.disconnects += 1
        state.last_disconnect_ms = timestamp_ms

    def record_message(self, venue: str, market: MarketType, *, timestamp_ms: int) -> None:
        state = self._state(venue, market)
        state.received_messages += 1
        state.last_message_ms = timestamp_ms

    def record_snapshot(self, venue: str, market: MarketType, *, timestamp_ms: int) -> None:
        state = self._state(venue, market)
        state.emitted_snapshots += 1
        state.last_snapshot_ms = timestamp_ms

    def record_parse_error(self, venue: str, market: MarketType) -> None:
        self._state(venue, market).parse_errors += 1

    def record_rate_limit(self, venue: str, market: MarketType) -> None:
        self._state(venue, market).rate_limit_events += 1

    def record_queue_drop(self, venue: str, market: MarketType) -> None:
        self._state(venue, market).queue_drops += 1

    def record_backoff(self, venue: str, market: MarketType, *, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("backoff milliseconds cannot be negative")
        self._state(venue, market).cumulative_backoff_ms += milliseconds

    def snapshots(self) -> tuple[StreamTelemetrySnapshot, ...]:
        return tuple(
            StreamTelemetrySnapshot(
                venue=venue,
                market=market,
                connection_attempts=state.connection_attempts,
                successful_connections=state.successful_connections,
                disconnects=state.disconnects,
                received_messages=state.received_messages,
                emitted_snapshots=state.emitted_snapshots,
                parse_errors=state.parse_errors,
                rate_limit_events=state.rate_limit_events,
                queue_drops=state.queue_drops,
                cumulative_backoff_ms=state.cumulative_backoff_ms,
                last_connected_ms=state.last_connected_ms,
                last_message_ms=state.last_message_ms,
                last_snapshot_ms=state.last_snapshot_ms,
                last_disconnect_ms=state.last_disconnect_ms,
            )
            for (venue, market), state in sorted(
                self._streams.items(),
                key=lambda item: (item[0][0], item[0][1].value),
            )
        )


def looks_rate_limited(error: BaseException) -> bool:
    """Conservatively identify common public-endpoint rate-limit failures."""

    candidates: list[Any] = [
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ]
    if any(value in {418, 429} for value in candidates):
        return True
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "429",
            "418",
            "rate limit",
            "too many requests",
            "too many connections",
        )
    )


def payload_looks_rate_limited(payload: object) -> bool:
    """Detect explicit exchange control/error payloads that mention throttling."""

    if not isinstance(payload, dict):
        return False
    code = payload.get("retCode", payload.get("code"))
    if str(code) in {"10006", "10429", "429", "418"}:
        return True
    message = str(payload.get("retMsg", payload.get("msg", ""))).lower()
    return any(token in message for token in ("rate limit", "too many requests", "too many"))
