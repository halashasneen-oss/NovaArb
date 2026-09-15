from decimal import Decimal

from novaarb.symbols import parse_spot_exchange_info


def test_exchange_info_parser_extracts_execution_rules() -> None:
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "filters": [
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.00001",
                        "stepSize": "0.00001",
                    },
                    {
                        "filterType": "MARKET_LOT_SIZE",
                        "minQty": "0.00010",
                        "stepSize": "0.00010",
                    },
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ],
            }
        ]
    }
    rules = parse_spot_exchange_info(payload)[0]
    assert rules.symbol == "BTCUSDT"
    assert rules.step_size == Decimal("0.00001")
    assert rules.min_notional == Decimal("5")
    assert rules.effective_market_step_size == Decimal("0.00010")
    assert rules.effective_market_min_quantity == Decimal("0.00010")
    assert rules.trading is True
