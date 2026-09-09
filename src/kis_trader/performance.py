from __future__ import annotations

from decimal import Decimal
from typing import Iterable

HUNDRED = Decimal("100")


def position_return(buy_price: Decimal, sell_price: Decimal) -> Decimal:
    if buy_price <= 0:
        raise ValueError("buy_price must be positive")
    if sell_price < 0:
        raise ValueError("sell_price cannot be negative")
    return sell_price / buy_price - Decimal("1")


def equal_weight_return(returns: Iterable[Decimal]) -> Decimal:
    values = list(returns)
    if not values:
        raise ValueError("at least one return is required")
    return sum(values, Decimal("0")) / Decimal(len(values))


def cumulative_index(previous_index: Decimal, daily_return: Decimal) -> Decimal:
    if previous_index < 0:
        raise ValueError("previous_index cannot be negative")
    return previous_index * (Decimal("1") + daily_return)


def net_return(
    buy_price: Decimal,
    sell_price: Decimal,
    quantity: int,
    commission: Decimal = Decimal("0"),
    tax: Decimal = Decimal("0"),
) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    cost = buy_price * quantity
    if cost <= 0:
        raise ValueError("buy notional must be positive")
    proceeds = sell_price * quantity - commission - tax
    return proceeds / cost - Decimal("1")

