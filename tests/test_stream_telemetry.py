from __future__ import annotations

from novaarb.domain import MarketType
from novaarb.stream_telemetry import (
    StreamTelemetry,
    looks_rate_limited,
    payload_looks_rate_limited,
)


def test_stream_telemetry_accumulates_reliability_evidence() -> None:
    telemetry = StreamTelemetry()
    telemetry.record_connection_attempt("binance", MarketType.SPOT)
    telemetry.record_connected("binance", MarketType.SPOT, timestamp_ms=100)
    telemetry.record_message("binance", MarketType.SPOT, timestamp_ms=110)
    telemetry.record_snapshot("binance", MarketType.SPOT, timestamp_ms=111)
    telemetry.record_parse_error("binance", MarketType.SPOT)
    telemetry.record_queue_drop("binance", MarketType.SPOT)
    telemetry.record_rate_limit("binance", MarketType.SPOT)
    telemetry.record_disconnect("binance", MarketType.SPOT, timestamp_ms=120)
    telemetry.record_backoff("binance", MarketType.SPOT, milliseconds=1000)

    snapshot = telemetry.snapshots()[0]
    assert snapshot.venue == "binance"
    assert snapshot.market is MarketType.SPOT
    assert snapshot.connection_attempts == 1
    assert snapshot.successful_connections == 1
    assert snapshot.disconnects == 1
    assert snapshot.received_messages == 1
    assert snapshot.emitted_snapshots == 1
    assert snapshot.parse_errors == 1
    assert snapshot.rate_limit_events == 1
    assert snapshot.queue_drops == 1
    assert snapshot.cumulative_backoff_ms == 1000
    assert snapshot.last_connected_ms == 100
    assert snapshot.last_message_ms == 110
    assert snapshot.last_snapshot_ms == 111
    assert snapshot.last_disconnect_ms == 120


def test_rate_limit_detection_covers_status_and_control_payloads() -> None:
    class TooManyRequests(Exception):
        status_code = 429

    assert looks_rate_limited(TooManyRequests("blocked")) is True
    assert looks_rate_limited(RuntimeError("HTTP 418 response")) is True
    assert looks_rate_limited(RuntimeError("network reset")) is False
    assert payload_looks_rate_limited({"retCode": 10429, "retMsg": "frequency protection"}) is True
    assert payload_looks_rate_limited({"code": 429, "msg": "Too many requests"}) is True
    assert payload_looks_rate_limited({"retCode": 0, "retMsg": "OK"}) is False


def test_stream_telemetry_rejects_negative_backoff() -> None:
    telemetry = StreamTelemetry()
    try:
        telemetry.record_backoff("bybit", MarketType.SPOT, milliseconds=-1)
    except ValueError as exc:
        assert "cannot be negative" in str(exc)
    else:
        raise AssertionError("negative backoff should fail")
