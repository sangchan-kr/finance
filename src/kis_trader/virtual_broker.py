from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .events import CompletedBar, Signal
from .ledger import Ledger


@dataclass
class VirtualPosition:
    quantity: int
    average_price: Decimal


@dataclass
class VirtualAccount:
    strategy_id: str
    strategy_version: str
    starting_cash: Decimal
    cash: Decimal
    realized_pnl: Decimal = Decimal(0)


@dataclass(frozen=True)
class BrokerConfig:
    starting_cash: Decimal
    allocation_per_trade: Decimal
    max_data_delay_seconds: Decimal
    fill_participation_rate: Decimal
    commission_rate: Decimal
    sell_tax_rate: Decimal
    fallback_spread_bps: Decimal


class VirtualBroker:
    def __init__(self, ledger: Ledger, config: BrokerConfig):
        self.ledger = ledger
        self.config = config
        self.accounts: dict[tuple[str, str], VirtualAccount] = {}
        self.positions: dict[tuple[str, str, str], VirtualPosition] = {}
        self.last_prices: dict[str, Decimal] = {}
        self._restore()

    def _restore(self) -> None:
        with self.ledger.connect() as connection:
            for row in connection.execute("SELECT * FROM virtual_accounts"):
                key = (row["strategy_id"], row["strategy_version"])
                self.accounts[key] = VirtualAccount(
                    *key,
                    Decimal(row["starting_cash"]),
                    Decimal(row["cash"]),
                    Decimal(row["realized_pnl"]),
                )
            for row in connection.execute("SELECT * FROM virtual_positions WHERE quantity > 0"):
                key = (row["strategy_id"], row["strategy_version"], row["symbol"])
                self.positions[key] = VirtualPosition(
                    int(row["quantity"]), Decimal(row["average_price"])
                )
            for row in connection.execute(
                """SELECT b.symbol,b.close FROM simulation_bars b JOIN
                (SELECT symbol,MAX(ended_at) ended_at FROM simulation_bars GROUP BY symbol) x
                ON b.symbol=x.symbol AND b.ended_at=x.ended_at"""
            ):
                self.last_prices[row["symbol"]] = Decimal(row["close"])

    def ensure_account(self, strategy_id: str, version: str, timestamp: str) -> VirtualAccount:
        key = (strategy_id, version)
        if key not in self.accounts:
            account = VirtualAccount(
                strategy_id,
                version,
                self.config.starting_cash,
                self.config.starting_cash,
            )
            self.accounts[key] = account
            with self.ledger.connect() as connection:
                connection.execute(
                    """INSERT OR IGNORE INTO virtual_accounts
                    (strategy_id,strategy_version,starting_cash,cash,realized_pnl,updated_at)
                    VALUES (?,?,?,?,?,?)""",
                    (strategy_id, version, str(account.starting_cash), str(account.cash), "0", timestamp),
                )
        return self.accounts[key]

    def position(self, strategy_id: str, version: str, symbol: str) -> VirtualPosition | None:
        return self.positions.get((strategy_id, version, symbol))

    def submit(self, signal: Signal) -> bool:
        order_id = hashlib.sha256(f"order|{signal.signal_id}".encode()).hexdigest()
        with self.ledger.connect() as connection:
            active = connection.execute(
                """SELECT 1 FROM virtual_orders WHERE strategy_id=? AND strategy_version=?
                AND symbol=? AND side=? AND status IN ('pending','partial')""",
                (signal.strategy_id, signal.strategy_version, signal.symbol, signal.side),
            ).fetchone()
            if active:
                return False
            inserted = connection.execute(
                """INSERT OR IGNORE INTO virtual_signals
                (signal_id,strategy_id,strategy_version,event_id,symbol,side,created_at,reason)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    signal.signal_id,
                    signal.strategy_id,
                    signal.strategy_version,
                    signal.event_id,
                    signal.symbol,
                    signal.side,
                    signal.created_at.isoformat(),
                    signal.reason,
                ),
            ).rowcount
            if not inserted:
                return False
            connection.execute(
                """INSERT INTO virtual_orders
                (order_id,signal_id,strategy_id,strategy_version,symbol,side,created_at,status)
                VALUES (?,?,?,?,?,?,?,'pending')""",
                (
                    order_id,
                    signal.signal_id,
                    signal.strategy_id,
                    signal.strategy_version,
                    signal.symbol,
                    signal.side,
                    signal.created_at.isoformat(),
                ),
            )
        return True

    def execute_pending(self, bar: CompletedBar) -> int:
        if bar.data_delay_seconds > self.config.max_data_delay_seconds:
            return 0
        with self.ledger.connect() as connection:
            rows = list(
                connection.execute(
                    """SELECT * FROM virtual_orders
                    WHERE symbol=? AND status IN ('pending','partial') AND created_at < ?
                    ORDER BY created_at, order_id""",
                    (bar.symbol, bar.ended_at.isoformat()),
                )
            )
        fills = 0
        for row in rows:
            if self._fill_order(dict(row), bar):
                fills += 1
        return fills

    def _execution_price(self, side: str, bar: CompletedBar) -> Decimal:
        if side == "buy" and bar.ask:
            return bar.ask
        if side == "sell" and bar.bid:
            return bar.bid
        half_spread = self.config.fallback_spread_bps / Decimal(20_000)
        return bar.close * (Decimal(1) + half_spread if side == "buy" else Decimal(1) - half_spread)

    def _fill_order(self, order: dict, bar: CompletedBar) -> bool:
        key = (order["strategy_id"], order["strategy_version"])
        account = self.ensure_account(*key, bar.ended_at.isoformat())
        position_key = (*key, order["symbol"])
        position = self.positions.get(position_key)
        price = self._execution_price(order["side"], bar)
        requested = int(order["requested_qty"])
        if requested == 0:
            if order["side"] == "buy":
                budget = min(self.config.allocation_per_trade, account.cash)
                requested = int((budget / price).to_integral_value(rounding=ROUND_DOWN))
            else:
                requested = position.quantity if position else 0
        remaining = requested - int(order["filled_qty"])
        quoted = bar.ask_quantity if order["side"] == "buy" else bar.bid_quantity
        liquidity = quoted if quoted is not None else int(bar.volume * self.config.fill_participation_rate)
        quantity = min(remaining, max(0, liquidity))
        if quantity <= 0:
            return False
        notional = price * quantity
        commission = notional * self.config.commission_rate
        tax = notional * self.config.sell_tax_rate if order["side"] == "sell" else Decimal(0)
        if order["side"] == "buy":
            affordable = int((account.cash / (price * (Decimal(1) + self.config.commission_rate))).to_integral_value(rounding=ROUND_DOWN))
            quantity = min(quantity, affordable)
            if quantity <= 0:
                return False
            notional = price * quantity
            commission = notional * self.config.commission_rate
            old_qty = position.quantity if position else 0
            old_cost = position.average_price * old_qty if position else Decimal(0)
            self.positions[position_key] = VirtualPosition(
                old_qty + quantity, (old_cost + notional) / (old_qty + quantity)
            )
            account.cash -= notional + commission
        else:
            if not position:
                return False
            quantity = min(quantity, position.quantity)
            notional = price * quantity
            commission = notional * self.config.commission_rate
            tax = notional * self.config.sell_tax_rate
            account.cash += notional - commission - tax
            account.realized_pnl += (
                (price - position.average_price) * quantity - commission - tax
            )
            remaining_position = position.quantity - quantity
            if remaining_position:
                self.positions[position_key] = VirtualPosition(remaining_position, position.average_price)
            else:
                self.positions.pop(position_key, None)
        total_filled = int(order["filled_qty"]) + quantity
        status = "filled" if total_filled >= requested else "partial"
        fill_id = hashlib.sha256(f"{order['order_id']}|{bar.event_id}".encode()).hexdigest()
        with self.ledger.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO virtual_fills
                (fill_id,order_id,strategy_id,strategy_version,event_id,symbol,side,filled_at,quantity,price,commission,tax)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fill_id, order["order_id"], *key, bar.event_id, order["symbol"], order["side"], bar.ended_at.isoformat(), quantity, str(price), str(commission), str(tax)),
            )
            connection.execute(
                "UPDATE virtual_orders SET requested_qty=?,filled_qty=?,status=? WHERE order_id=?",
                (requested, total_filled, status, order["order_id"]),
            )
            pos = self.positions.get(position_key)
            if pos:
                connection.execute(
                    """INSERT INTO virtual_positions VALUES (?,?,?,?,?,?)
                    ON CONFLICT(strategy_id,strategy_version,symbol) DO UPDATE SET
                    quantity=excluded.quantity,average_price=excluded.average_price,updated_at=excluded.updated_at""",
                    (*key, order["symbol"], pos.quantity, str(pos.average_price), bar.ended_at.isoformat()),
                )
            else:
                connection.execute(
                    "DELETE FROM virtual_positions WHERE strategy_id=? AND strategy_version=? AND symbol=?",
                    (*key, order["symbol"]),
                )
            connection.execute(
                "UPDATE virtual_accounts SET cash=?,realized_pnl=?,updated_at=? WHERE strategy_id=? AND strategy_version=?",
                (str(account.cash), str(account.realized_pnl), bar.ended_at.isoformat(), *key),
            )
        return True

    def mark_equity(self, strategy_id: str, version: str, bar: CompletedBar) -> Decimal:
        account = self.ensure_account(strategy_id, version, bar.ended_at.isoformat())
        self.last_prices[bar.symbol] = bar.close
        unrealized = Decimal(0)
        market_value = Decimal(0)
        for (sid, ver, symbol), position in self.positions.items():
            if (sid, ver) == (strategy_id, version):
                mark = self.last_prices.get(symbol, position.average_price)
                market_value += mark * position.quantity
                unrealized += (mark - position.average_price) * position.quantity
        equity = account.cash + market_value
        with self.ledger.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO strategy_equity
                (strategy_id,strategy_version,event_id,measured_at,equity,cash,unrealized_pnl)
                VALUES (?,?,?,?,?,?,?)""",
                (strategy_id, version, bar.event_id, bar.ended_at.isoformat(), str(equity), str(account.cash), str(unrealized)),
            )
        return equity
