from novaarb.binance import BinanceDepthStream
from novaarb.domain import MarketType


def test_parse_combined_partial_depth_event() -> None:
    stream = BinanceDepthStream(symbols=("BTCUSDT",), market=MarketType.SPOT)
    payload = {
        "stream": "btcusdt@depth20@100ms",
        "data": {
            "E": 123,
            "bids": [["100", "2"], ["99", "3"]],
            "asks": [["101", "4"], ["102", "5"]],
        },
    }
    book = stream._parse(payload, 150)
    assert book.symbol == "BTCUSDT"
    assert str(book.best_bid.price) == "100"
    assert str(book.best_ask.price) == "101"
    assert book.received_time_ms == 150
