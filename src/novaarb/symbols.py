from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.request import Request, urlopen


SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    step_size: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    market_step_size: Decimal = Decimal("0")
    market_min_quantity: Decimal = Decimal("0")

    @property
    def trading(self) -> bool:
        return self.status == "TRADING"

    @property
    def effective_market_step_size(self) -> Decimal:
        return self.market_step_size if self.market_step_size > 0 else self.step_size

    @property
    def effective_market_min_quantity(self) -> Decimal:
        return self.market_min_quantity if self.market_min_quantity > 0 else self.min_quantity


def _filter_map(filters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("filterType")): item for item in filters}


def parse_spot_exchange_info(payload: dict[str, Any]) -> tuple[SymbolRules, ...]:
    output: list[SymbolRules] = []
    for raw in payload.get("symbols", []):
        filters = _filter_map(raw.get("filters", []))
        lot = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        output.append(
            SymbolRules(
                symbol=str(raw["symbol"]).upper(),
                base_asset=str(raw["baseAsset"]).upper(),
                quote_asset=str(raw["quoteAsset"]).upper(),
                status=str(raw.get("status", "")),
                step_size=Decimal(str(lot.get("stepSize", "0"))),
                min_quantity=Decimal(str(lot.get("minQty", "0"))),
                min_notional=Decimal(str(notional.get("minNotional", "0"))),
                market_step_size=Decimal(str(market_lot.get("stepSize", "0"))),
                market_min_quantity=Decimal(str(market_lot.get("minQty", "0"))),
            )
        )
    return tuple(output)


def fetch_spot_exchange_info(timeout: float = 10.0) -> tuple[SymbolRules, ...]:
    request = Request(SPOT_EXCHANGE_INFO_URL, headers={"User-Agent": "NovaArb/0.3"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Binance HTTPS URL
        payload = json.loads(response.read().decode("utf-8"))
    return parse_spot_exchange_info(payload)
