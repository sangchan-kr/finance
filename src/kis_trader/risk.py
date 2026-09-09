from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .config import Settings
from .ledger import Ledger


class RiskRejected(ValueError):
    """Raised when an order intent violates a mandatory risk rule."""


@dataclass(frozen=True)
class OrderIntent:
    trade_date: date
    symbol: str
    side: str
    quantity: int
    price: Decimal
    environment: str
    spread_bps: Decimal | None = None

    @property
    def amount(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class DailyRiskState:
    invested_amount: Decimal = Decimal("0")
    realized_loss: Decimal = Decimal("0")
    distinct_symbols: int = 0


class RiskGate:
    def __init__(self, settings: Settings, ledger: Ledger):
        self.settings = settings
        self.ledger = ledger

    def validate(self, intent: OrderIntent, state: DailyRiskState) -> None:
        risk = self.settings.risk
        if self.settings.mode == "market-data-only" or not self.settings.order_enabled:
            raise RiskRejected("orders are disabled")
        if risk.stop_file.exists():
            raise RiskRejected("emergency stop file exists")
        if intent.environment != self.settings.account_environment:
            raise RiskRejected("order and account environments do not match")
        if intent.side not in {"buy", "sell"}:
            raise RiskRejected("side must be buy or sell")
        if not intent.symbol.isdigit() or len(intent.symbol) != 6:
            raise RiskRejected("symbol must contain exactly 6 digits")
        if risk.allowed_symbols and intent.symbol not in risk.allowed_symbols:
            raise RiskRejected("symbol is not allowlisted")
        if intent.quantity <= 0 or intent.price <= 0:
            raise RiskRejected("quantity and price must be positive")
        if risk.max_order_amount <= 0 or intent.amount > risk.max_order_amount:
            raise RiskRejected("order amount limit is unset or exceeded")
        if intent.side == "buy":
            if risk.max_daily_investment <= 0:
                raise RiskRejected("daily investment limit is unset")
            if state.invested_amount + intent.amount > risk.max_daily_investment:
                raise RiskRejected("daily investment limit exceeded")
            if state.distinct_symbols >= risk.max_symbols_per_day:
                raise RiskRejected("daily symbol limit exceeded")
        if risk.max_daily_loss <= 0 or state.realized_loss >= risk.max_daily_loss:
            raise RiskRejected("daily loss limit is unset or reached")
        if intent.spread_bps is not None and intent.spread_bps > risk.max_spread_bps:
            raise RiskRejected("spread limit exceeded")
        trade_date = intent.trade_date.isoformat()
        if self.ledger.has_order(trade_date, intent.symbol, intent.side):
            raise RiskRejected("duplicate order")
        self.settings.assert_orders_locked()

