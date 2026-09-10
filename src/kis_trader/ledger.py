from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    rank INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    entry_time TEXT NOT NULL,
    exit_order_time TEXT NOT NULL,
    invalidation_rule TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    environment TEXT NOT NULL CHECK (environment IN ('paper', 'live')),
    requested_at TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_qty INTEGER NOT NULL CHECK (requested_qty > 0),
    requested_price TEXT NOT NULL,
    kis_order_no TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    UNIQUE (trade_date, symbol, side)
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kis_order_no TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    filled_at TEXT NOT NULL,
    filled_qty INTEGER NOT NULL CHECK (filled_qty > 0),
    filled_price TEXT NOT NULL,
    commission TEXT NOT NULL DEFAULT '0',
    tax TEXT NOT NULL DEFAULT '0',
    UNIQUE (kis_order_no, filled_at, filled_qty, filled_price)
);

CREATE TABLE IF NOT EXISTS daily_performance (
    trade_date TEXT PRIMARY KEY,
    gross_return TEXT NOT NULL,
    net_return TEXT,
    cumulative_index TEXT NOT NULL,
    max_intraday_drawdown TEXT,
    valid_symbol_count INTEGER NOT NULL,
    verification_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seed_trades (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    buy_basis TEXT NOT NULL,
    buy_price TEXT NOT NULL,
    sell_basis TEXT NOT NULL,
    sell_price TEXT NOT NULL,
    gross_return TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('real', 'demo')),
    symbol TEXT NOT NULL,
    price TEXT NOT NULL,
    change_rate TEXT,
    volume TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE (collected_at, environment, symbol)
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_time
ON market_snapshots(symbol, collected_at DESC);

CREATE TABLE IF NOT EXISTS simulation_events (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulation_bars (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume INTEGER NOT NULL,
    bid TEXT,
    ask TEXT,
    bid_quantity INTEGER,
    ask_quantity INTEGER,
    received_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES simulation_events(event_id)
);

CREATE TABLE IF NOT EXISTS virtual_accounts (
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    starting_cash TEXT NOT NULL,
    cash TEXT NOT NULL,
    realized_pnl TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (strategy_id, strategy_version)
);

CREATE TABLE IF NOT EXISTS virtual_positions (
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    average_price TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (strategy_id, strategy_version, symbol)
);

CREATE TABLE IF NOT EXISTS virtual_signals (
    signal_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES simulation_events(event_id)
);

CREATE TABLE IF NOT EXISTS virtual_orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    created_at TEXT NOT NULL,
    requested_qty INTEGER NOT NULL DEFAULT 0,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES virtual_signals(signal_id)
);

CREATE TABLE IF NOT EXISTS virtual_fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    filled_at TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price TEXT NOT NULL,
    commission TEXT NOT NULL,
    tax TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES virtual_orders(order_id),
    FOREIGN KEY (event_id) REFERENCES simulation_events(event_id),
    UNIQUE (order_id, event_id)
);

CREATE TABLE IF NOT EXISTS strategy_equity (
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    equity TEXT NOT NULL,
    cash TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    PRIMARY KEY (strategy_id, strategy_version, event_id),
    FOREIGN KEY (event_id) REFERENCES simulation_events(event_id)
);
"""

SEED_SQL = """
INSERT OR IGNORE INTO seed_trades
(trade_date, symbol, name, buy_basis, buy_price, sell_basis, sell_price, gross_return)
VALUES
('2026-09-09', '005930', '삼성전자', '13:00 체결가', '270000', '15:30 종가', '269500', '-0.00185185185185185185'),
('2026-09-09', '009150', '삼성전기', '13:00 체결가', '1401000', '15:30 종가', '1404000', '0.00214132762312633833'),
('2026-09-09', '042660', '한화오션', '13:00 체결가', '87700', '15:30 종가', '88100', '0.00456036488027366021');

INSERT OR IGNORE INTO daily_performance
(trade_date, gross_return, net_return, cumulative_index, max_intraday_drawdown,
 valid_symbol_count, verification_status)
VALUES ('2026-09-09', '0.00161682639734235068', NULL,
        '100.161682639734235068', NULL, 3, 'manual-unverified');
"""


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, include_seed_data: bool = True) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )
            if include_seed_data:
                connection.executescript(SEED_SQL)

    def has_order(self, trade_date: str, symbol: str, side: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM orders WHERE trade_date=? AND symbol=? AND side=?",
                (trade_date, symbol, side),
            ).fetchone()
        return row is not None

    def save_market_snapshot(
        self,
        *,
        collected_at: str,
        environment: str,
        symbol: str,
        price: str,
        change_rate: str | None,
        volume: str | None,
        raw_json: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO market_snapshots
                (collected_at, environment, symbol, price, change_rate, volume, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (collected_at, environment, symbol, price, change_rate, volume, raw_json),
            )

    def recent_snapshots(self, limit: int = 100) -> list[sqlite3.Row]:
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT collected_at, environment, symbol, price, change_rate, volume
                    FROM market_snapshots ORDER BY collected_at DESC, symbol LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )
