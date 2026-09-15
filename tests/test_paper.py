from __future__ import annotations

from decimal import Decimal

from novaarb.paper import PaperLedger


def test_paper_ledger_tracks_drawdown_and_profit_factor() -> None:
    ledger = PaperLedger(Decimal("1000"))
    ledger.apply(
        route_id="A>B>C>A",
        signal_time_ms=1,
        completion_ms=10,
        net_profit=Decimal("20"),
        realized_edge_bps=Decimal("20"),
    )
    ledger.apply(
        route_id="A>B>C>A",
        signal_time_ms=20,
        completion_ms=30,
        net_profit=Decimal("-10"),
        realized_edge_bps=Decimal("-10"),
    )
    ledger.apply(
        route_id="A>D>C>A",
        signal_time_ms=40,
        completion_ms=50,
        net_profit=Decimal("5"),
        realized_edge_bps=Decimal("5"),
    )

    assert ledger.balance == Decimal("1015")
    assert ledger.gross_profit == Decimal("25")
    assert ledger.gross_loss == Decimal("10")
    assert ledger.profit_factor == Decimal("2.5")
    assert ledger.max_drawdown_pct > 0
    assert len(ledger.trades) == 3
