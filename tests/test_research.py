from decimal import Decimal

from novaarb.domain import MarketType
from novaarb.research import (
    OpportunityWindowTracker,
    ResearchRecorder,
    iter_records,
    snapshot_from_record,
)
from conftest import make_book


def test_research_log_roundtrip(tmp_path) -> None:
    path = tmp_path / "session.jsonl.gz"
    book = make_book(market=MarketType.SPOT, bid="99", ask="100")
    recorder = ResearchRecorder(path)
    recorder.append_book(book)

    records = list(iter_records(path))
    assert len(records) == 1
    restored = snapshot_from_record(records[0])
    assert restored == book


def test_opportunity_observations_collapse_into_windows() -> None:
    tracker = OpportunityWindowTracker(gap_tolerance_ms=500)
    for timestamp in (1_000, 1_100, 1_250):
        tracker.observe(
            symbol="BTCUSDT",
            buy_market=MarketType.SPOT,
            sell_market=MarketType.PERPETUAL,
            timestamp_ms=timestamp,
            approved=True,
            edge_bps=Decimal("3"),
        )
    tracker.observe(
        symbol="BTCUSDT",
        buy_market=MarketType.SPOT,
        sell_market=MarketType.PERPETUAL,
        timestamp_ms=1_300,
        approved=False,
        edge_bps=Decimal("1"),
    )
    windows = tracker.finish()
    assert len(windows) == 1
    assert windows[0].observations == 3
    assert windows[0].duration_ms == 250


def test_research_metadata_roundtrip(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_metadata("config", {"fee_bps": Decimal("10")})
    record = list(iter_records(path))[0]
    assert record["kind"] == "metadata"
    assert record["name"] == "config"
    assert record["payload"]["fee_bps"] == "10"
