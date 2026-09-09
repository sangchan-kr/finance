from datetime import date
from decimal import Decimal

import pytest
from conftest import make_settings

from kis_trader.ledger import Ledger
from kis_trader.risk import DailyRiskState, OrderIntent, RiskGate, RiskRejected


def intent(**changes):
    values = {
        "trade_date": date(2026, 9, 9),
        "symbol": "005930",
        "side": "buy",
        "quantity": 2,
        "price": Decimal("100000"),
        "environment": "paper",
        "spread_bps": Decimal("10"),
    }
    values.update(changes)
    return OrderIntent(**values)


def test_market_data_only_always_rejects_orders(tmp_path):
    settings = make_settings(tmp_path)
    ledger = Ledger(settings.storage.database_path)
    ledger.initialize(False)
    with pytest.raises(RiskRejected, match="disabled"):
        RiskGate(settings, ledger).validate(intent(), DailyRiskState())


def test_order_amount_limit(tmp_path):
    settings = make_settings(tmp_path, mode="trading", order_enabled=True)
    ledger = Ledger(settings.storage.database_path)
    ledger.initialize(False)
    with pytest.raises(RiskRejected, match="order amount"):
        RiskGate(settings, ledger).validate(
            intent(quantity=11), DailyRiskState(realized_loss=Decimal("0"))
        )


def test_duplicate_order_is_rejected(tmp_path):
    settings = make_settings(tmp_path, mode="trading", order_enabled=True)
    ledger = Ledger(settings.storage.database_path)
    ledger.initialize(False)
    row = ("id-1", "2026-09-09", "005930", "buy", "paper", "2026-09-09T09:10:00", "limit", 1, "100", "submitted")
    with ledger.connect() as connection:
        connection.execute("INSERT INTO orders(client_order_id,trade_date,symbol,side,environment,requested_at,order_type,requested_qty,requested_price,status) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    with pytest.raises(RiskRejected, match="duplicate"):
        RiskGate(settings, ledger).validate(intent(), DailyRiskState())

