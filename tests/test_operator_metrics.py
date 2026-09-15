from __future__ import annotations

from novaarb.domain import MarketType
from novaarb.operator_metrics import TelemetryResearchRecorder, stream_telemetry_payload
from novaarb.research import iter_records
from novaarb.stream_telemetry import StreamTelemetry


def _telemetry() -> StreamTelemetry:
    telemetry = StreamTelemetry()
    telemetry.record_connection_attempt("binance", MarketType.SPOT)
    telemetry.record_connected("binance", MarketType.SPOT, timestamp_ms=100)
    telemetry.record_message("binance", MarketType.SPOT, timestamp_ms=110)
    telemetry.record_snapshot("binance", MarketType.SPOT, timestamp_ms=111)
    telemetry.record_disconnect("binance", MarketType.SPOT, timestamp_ms=120)
    telemetry.record_backoff("binance", MarketType.SPOT, milliseconds=1_000)
    telemetry.record_rate_limit("binance", MarketType.SPOT)
    telemetry.record_queue_drop("binance", MarketType.SPOT)
    return telemetry


def test_stream_telemetry_payload_is_json_ready() -> None:
    payload = stream_telemetry_payload(_telemetry())

    assert payload == [
        {
            "venue": "binance",
            "market": "spot",
            "connection_attempts": 1,
            "successful_connections": 1,
            "disconnects": 1,
            "received_messages": 1,
            "emitted_snapshots": 1,
            "parse_errors": 0,
            "rate_limit_events": 1,
            "queue_drops": 1,
            "cumulative_backoff_ms": 1_000,
            "last_connected_ms": 100,
            "last_message_ms": 110,
            "last_snapshot_ms": 111,
            "last_disconnect_ms": 120,
        }
    ]


def test_telemetry_research_recorder_enriches_heartbeat(tmp_path) -> None:
    path = tmp_path / "metrics.jsonl"
    recorder = TelemetryResearchRecorder(path, telemetry=_telemetry())

    recorder.append_metadata("shadow_heartbeat", {"executed_trades": 3})

    record = next(iter_records(path))
    assert record["kind"] == "metadata"
    assert record["name"] == "shadow_heartbeat"
    assert record["payload"]["executed_trades"] == 3
    assert record["payload"]["stream_telemetry"][0]["disconnects"] == 1
    assert record["payload"]["stream_telemetry"][0]["rate_limit_events"] == 1
