from __future__ import annotations

from decimal import Decimal

from novaarb.cross_venue import CrossVenueConfig, VenueCostProfile
from novaarb.cross_venue_survival import analyze_cross_venue_survival
from novaarb.domain import BookLevel, MarketType, OrderBookSnapshot
from novaarb.research import ResearchRecorder


def _book(*, venue: str, bid: str, ask: str, received_ms: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        venue=venue,
        symbol="BTCUSDT",
        market=MarketType.SPOT,
        bids=(BookLevel(Decimal(bid), Decimal("10")),),
        asks=(BookLevel(Decimal(ask), Decimal("10")),),
        event_time_ms=received_ms,
        received_time_ms=received_ms,
    )


def _costs() -> tuple[VenueCostProfile, ...]:
    return (
        VenueCostProfile("alpha", Decimal("1")),
        VenueCostProfile("beta", Decimal("1")),
    )


def _config() -> CrossVenueConfig:
    return CrossVenueConfig(
        target_notional_quote=Decimal("100"),
        min_net_edge_bps=Decimal("1"),
        max_book_age_ms=500,
        max_book_skew_ms=150,
    )


def test_cross_venue_survival_measures_window_lifetime_by_direction(tmp_path) -> None:
    path = tmp_path / "cross.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_000))
    recorder.append_book(_book(venue="beta", bid="102", ask="103", received_ms=1_000))
    recorder.append_book(
        _book(venue="alpha", bid="99.1", ask="100.1", received_ms=1_050)
    )
    recorder.append_book(
        _book(venue="beta", bid="101.8", ask="102.8", received_ms=1_050)
    )
    recorder.append_book(
        _book(venue="alpha", bid="101", ask="102", received_ms=1_150)
    )

    report = analyze_cross_venue_survival(
        str(path),
        base_asset="BTC",
        quote_asset="USDT",
        costs=_costs(),
        strategy_config=_config(),
        gap_tolerance_ms=100,
        thresholds_ms=(0, 25, 50, 100),
    )

    assert report.windows == 1
    assert report.median_duration_ms == Decimal("50")
    assert report.max_duration_ms == 50
    assert report.median_peak_edge_bps > 0
    assert [item.surviving_windows for item in report.survival_curve] == [1, 1, 1, 0]
    assert [item.survival_rate for item in report.survival_curve] == [
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
    ]
    assert len(report.directions) == 1
    direction = report.directions[0]
    assert direction.buy_venue == "alpha"
    assert direction.sell_venue == "beta"
    assert direction.window_count == 1
    assert direction.max_duration_ms == 50


def test_cross_venue_survival_returns_zero_curve_without_approved_edge(tmp_path) -> None:
    path = tmp_path / "flat.jsonl"
    recorder = ResearchRecorder(path)
    recorder.append_book(_book(venue="alpha", bid="99", ask="100", received_ms=1_000))
    recorder.append_book(_book(venue="beta", bid="99", ask="100", received_ms=1_000))

    report = analyze_cross_venue_survival(
        str(path),
        base_asset="BTC",
        quote_asset="USDT",
        costs=_costs(),
        strategy_config=_config(),
        thresholds_ms=(0, 50),
    )

    assert report.windows == 0
    assert report.directions == ()
    assert [item.survival_rate for item in report.survival_curve] == [
        Decimal("0"),
        Decimal("0"),
    ]
