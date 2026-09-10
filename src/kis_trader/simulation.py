from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .events import CompletedBar
from .ledger import Ledger
from .strategies import Strategy, strategies_from_config
from .virtual_broker import BrokerConfig, VirtualBroker


def build_simulation_engine(settings, ledger: Ledger) -> "SimulationEngine":
    simulation = settings.simulation
    broker = VirtualBroker(
        ledger,
        BrokerConfig(
            starting_cash=Decimal(simulation.starting_cash),
            allocation_per_trade=Decimal(simulation.allocation_per_trade),
            max_data_delay_seconds=Decimal(simulation.max_data_delay_seconds),
            fill_participation_rate=Decimal(str(simulation.fill_participation_rate)),
            commission_rate=Decimal(str(simulation.commission_rate)),
            sell_tax_rate=Decimal(str(simulation.sell_tax_rate)),
            fallback_spread_bps=Decimal(simulation.fallback_spread_bps),
        ),
    )
    return SimulationEngine(ledger, broker, strategies_from_config(simulation.strategies))


class SimulationEngine:
    """One event loop shared by historical replay and live paper operation."""

    def __init__(self, ledger: Ledger, broker: VirtualBroker, strategies: list[Strategy]):
        self.ledger = ledger
        self.broker = broker
        self.strategies = strategies
        self._restore_intraday_strategy_state()

    def _restore_intraday_strategy_state(self) -> None:
        with self.ledger.connect() as connection:
            rows = list(connection.execute("SELECT * FROM simulation_bars ORDER BY ended_at"))
        if not rows:
            return
        latest_date = datetime.fromisoformat(rows[-1]["ended_at"]).date()
        for row in rows:
            if datetime.fromisoformat(row["ended_at"]).date() != latest_date:
                continue
            bar = self._row_to_bar(row)
            for strategy in self.strategies:
                position = self.broker.position(strategy.strategy_id, strategy.version, bar.symbol)
                strategy.on_bar(bar, position)

    @staticmethod
    def _row_to_bar(row) -> CompletedBar:
        return CompletedBar(
            symbol=row["symbol"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=int(row["volume"]),
            bid=Decimal(row["bid"]) if row["bid"] else None,
            ask=Decimal(row["ask"]) if row["ask"] else None,
            bid_quantity=row["bid_quantity"],
            ask_quantity=row["ask_quantity"],
            received_at=datetime.fromisoformat(row["received_at"]),
        )

    def on_bar(self, bar: CompletedBar) -> bool:
        with self.ledger.connect() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO simulation_events(event_id,symbol,ended_at) VALUES (?,?,?)",
                (bar.event_id, bar.symbol, bar.ended_at.isoformat()),
            ).rowcount
            if not inserted:
                return False
            connection.execute(
                """INSERT INTO simulation_bars
                (event_id,symbol,started_at,ended_at,open,high,low,close,volume,bid,ask,
                 bid_quantity,ask_quantity,received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    bar.event_id,
                    bar.symbol,
                    bar.started_at.isoformat(),
                    bar.ended_at.isoformat(),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    bar.volume,
                    str(bar.bid) if bar.bid else None,
                    str(bar.ask) if bar.ask else None,
                    bar.bid_quantity,
                    bar.ask_quantity,
                    (bar.received_at or bar.ended_at).isoformat(),
                ),
            )
        self.broker.execute_pending(bar)
        if bar.data_delay_seconds > self.broker.config.max_data_delay_seconds:
            return True
        for strategy in self.strategies:
            self.broker.ensure_account(
                strategy.strategy_id, strategy.version, bar.ended_at.isoformat()
            )
            position = self.broker.position(strategy.strategy_id, strategy.version, bar.symbol)
            for signal in strategy.on_bar(bar, position):
                self.broker.submit(signal)
            self.broker.mark_equity(strategy.strategy_id, strategy.version, bar)
        return True
