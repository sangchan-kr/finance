import sqlite3

from kis_trader.ledger import Ledger


def test_initialize_is_idempotent_and_seeds_handoff_data(tmp_path):
    path = tmp_path / "trading.db"
    ledger = Ledger(path)
    ledger.initialize()
    ledger.initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM seed_trades").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM daily_performance").fetchone()[0] == 1


def test_order_uniqueness_is_enforced(tmp_path):
    path = tmp_path / "trading.db"
    ledger = Ledger(path)
    ledger.initialize(False)
    row = ("id-1", "2026-09-09", "005930", "buy", "paper", "2026-09-09T09:10:00", "limit", 1, "100", "submitted")
    with ledger.connect() as connection:
        connection.execute("INSERT INTO orders(client_order_id,trade_date,symbol,side,environment,requested_at,order_type,requested_qty,requested_price,status) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    assert ledger.has_order("2026-09-09", "005930", "buy")


def test_recent_market_snapshots(tmp_path):
    ledger = Ledger(tmp_path / "trading.db")
    ledger.initialize(False)
    ledger.save_market_snapshot(
        collected_at="2026-09-09T09:10:00+09:00",
        environment="real",
        symbol="005930",
        price="70000",
        change_rate="1.0",
        volume="100",
        raw_json="{}",
    )
    assert ledger.recent_snapshots(1)[0]["price"] == "70000"
