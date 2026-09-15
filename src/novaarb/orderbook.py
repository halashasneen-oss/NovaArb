from __future__ import annotations

from decimal import Decimal

from novaarb.domain import BookFill, OrderBookSnapshot, Side, ZERO


class InsufficientLiquidity(ValueError):
    """Raised when requested base quantity cannot be filled from visible depth."""


def simulate_base_fill(
    book: OrderBookSnapshot,
    side: Side,
    base_quantity: Decimal,
) -> BookFill:
    if base_quantity <= ZERO:
        raise ValueError("base quantity must be positive")

    levels = book.asks if side is Side.BUY else book.bids
    remaining = base_quantity
    quote = ZERO
    levels_used = 0

    for level in levels:
        take = min(remaining, level.quantity)
        if take > ZERO:
            quote += take * level.price
            remaining -= take
            levels_used += 1
        if remaining == ZERO:
            break

    if remaining > ZERO:
        raise InsufficientLiquidity(
            f"{book.venue}:{book.market}:{book.symbol} missing {remaining} base units"
        )

    average_price = quote / base_quantity
    top_price = levels[0].price
    if side is Side.BUY:
        slippage = quote - (top_price * base_quantity)
    else:
        slippage = (top_price * base_quantity) - quote

    return BookFill(
        side=side,
        base_quantity=base_quantity,
        quote_quantity=quote,
        average_price=average_price,
        top_price=top_price,
        depth_slippage_usd=max(ZERO, slippage),
        levels_used=levels_used,
    )
