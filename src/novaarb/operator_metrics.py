from __future__ import annotations

from pathlib import Path

from novaarb.research import ResearchRecorder
from novaarb.stream_telemetry import StreamTelemetry


def stream_telemetry_payload(telemetry: StreamTelemetry) -> list[dict[str, object]]:
    """Return stable JSON-ready public-stream reliability metrics."""

    return [
        {
            "venue": snapshot.venue,
            "market": snapshot.market.value,
            "connection_attempts": snapshot.connection_attempts,
            "successful_connections": snapshot.successful_connections,
            "disconnects": snapshot.disconnects,
            "received_messages": snapshot.received_messages,
            "emitted_snapshots": snapshot.emitted_snapshots,
            "parse_errors": snapshot.parse_errors,
            "rate_limit_events": snapshot.rate_limit_events,
            "queue_drops": snapshot.queue_drops,
            "cumulative_backoff_ms": snapshot.cumulative_backoff_ms,
            "last_connected_ms": snapshot.last_connected_ms,
            "last_message_ms": snapshot.last_message_ms,
            "last_snapshot_ms": snapshot.last_snapshot_ms,
            "last_disconnect_ms": snapshot.last_disconnect_ms,
        }
        for snapshot in telemetry.snapshots()
    ]


class TelemetryResearchRecorder(ResearchRecorder):
    """Research recorder that embeds current public-stream telemetry in metadata events."""

    def __init__(self, path: str | Path, *, telemetry: StreamTelemetry) -> None:
        super().__init__(path)
        self.telemetry = telemetry

    def append_metadata(self, name: str, payload: object) -> None:
        if isinstance(payload, dict):
            enriched: dict[str, object] = dict(payload)
        else:
            enriched = {"value": payload}
        enriched["stream_telemetry"] = stream_telemetry_payload(self.telemetry)
        super().append_metadata(name, enriched)
