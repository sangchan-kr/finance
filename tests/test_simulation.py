from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from kis_trader.events import CompletedBar, make_signal
from kis_trader.ledger import Ledger
from kis_trader.simulation import SimulationEngine
from kis_trader.strategies import OrbStrategy, VwapPullbackStrategy
from kis_trader.virtual_broker import BrokerConfig, VirtualBroker

KST = ZoneInfo("Asia/Seoul")


def bar(minute, close=100, high=None, low=None, volume=1000, bid=None, ask=None, bid_qty=None, ask_qty=None, delay=0):
    ended = datetime(2026, 9, 9, 9, 0, tzinfo=KST) + timedelta(minutes=minute)
    close = Decimal(str(close))
    return CompletedBar(
        "005930",
        ended - timedelta(minutes=1),
        ended,
        close,
        Decimal(str(high if high is not None else close)),
        Decimal(str(low if low is not None else close)),
        close,
        volume,
        Decimal(str(bid)) if bid is not None else None,
        Decimal(str(ask)) if ask is not None else None,
        bid_qty,
        ask_qty,
        ended + timedelta(seconds=delay),
    )


def broker(tmp_path, **overrides):
    ledger = Ledger(tmp_path / "simulation.db")
    ledger.initialize(False)
    values = dict(
        starting_cash=Decimal("10000"),
        allocation_per_trade=Decimal("1000"),
        max_data_delay_seconds=Decimal("10"),
        fill_participation_rate=Decimal("0.25"),
        commission_rate=Decimal("0.001"),
        sell_tax_rate=Decimal("0.002"),
        fallback_spread_bps=Decimal("10"),
    )
    values.update(overrides)
    return ledger, VirtualBroker(ledger, BrokerConfig(**values))


class OneShotStrategy:
    strategy_id = "one"
    version = "1.0.0"

    def __init__(self):
        self.sent = False

    def on_bar(self, event, position):
        if not self.sent and position is None:
            self.sent = True
            return [make_signal(self.strategy_id, self.version, event, "buy", "test")]
        return []


class RoundTripStrategy:
    strategy_id = "roundtrip"
    version = "1"

    def on_bar(self, event, position):
        if event.ended_at.minute == 1:
            return [make_signal(self.strategy_id, self.version, event, "buy", "entry")]
        if event.ended_at.minute == 2 and position:
            return [make_signal(self.strategy_id, self.version, event, "sell", "exit")]
        return []


def test_signal_only_fills_on_later_bar_at_ask(tmp_path):
    ledger, virtual = broker(tmp_path)
    engine = SimulationEngine(ledger, virtual, [OneShotStrategy()])
    first = bar(1, close=100, ask=101, ask_qty=10)
    assert engine.on_bar(first)
    assert virtual.position("one", "1.0.", "005930") is None
    second = bar(2, close=102, ask=103, ask_qty=10)
    engine.on_bar(second)
    position = virtual.position("one", "1.0.0", "005930")
    assert position is not None
    assert position.average_price == Decimal("103")


def test_partial_fill_costs_and_restart_recovery(tmp_path):
    ledger, virtual = broker(tmp_path)
    engine = SimulationEngine(ledger, virtual, [OneShotStrategy()])
    engine.on_bar(bar(1, ask=100, ask_qty=1))
    engine.on_bar(bar(2, ask=100, ask_qty=3))
    position = virtual.position("one", "1.0.0", "005930")
    assert position and position.quantity == 3
    assert virtual.accounts[("one", "1.0.0")].cash == Decimal("9699.700")
    restored = VirtualBroker(ledger, virtual.config)
    assert restored.position("one", "1.0.0", "005930").quantity == 3
    assert restored.accounts[("one", "1.0.0")].cash == Decimal("9699.700")


def test_sell_uses_bid_and_applies_commission_and_tax(tmp_path):
    ledger, virtual = broker(tmp_path)
    engine = SimulationEngine(ledger, virtual, [RoundTripStrategy()])
    engine.on_bar(bar(1, ask=100, ask_qty=10))
    engine.on_bar(bar(2, ask=100, ask_qty=10))
    engine.on_bar(bar(3, bid=110, bid_qty=10))
    account = virtual.accounts[("roundtrip", "1")]
    assert account.cash == Decimal("10095.700")
    with ledger.connect() as connection:
        sell = connection.execute(
            "SELECT price,commission,tax FROM virtual_fills WHERE side='sell'"
        ).fetchone()
    assert tuple(sell) == ("110", "1.100", "2.200")


def test_duplicate_event_and_duplicate_active_order_are_ignored(tmp_path):
    ledger, virtual = broker(tmp_path)
    strategy = OneShotStrategy()
    engine = SimulationEngine(ledger, virtual, [strategy])
    event = bar(1)
    assert engine.on_bar(event)
    assert not engine.on_bar(event)
    with ledger.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0] == 1


def test_delayed_event_is_recorded_but_does_not_create_signal(tmp_path):
    ledger, virtual = broker(tmp_path, max_data_delay_seconds=Decimal("0"))
    engine = SimulationEngine(ledger, virtual, [OneShotStrategy()])
    assert engine.on_bar(bar(1, delay=1))
    with ledger.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0] == 0


def test_orb_uses_completed_opening_range_then_breakout():
    strategy = OrbStrategy(breakout_buffer_bps=Decimal(0))
    assert strategy.on_bar(bar(10, close=100, high=101, low=99), None) == []
    assert strategy.on_bar(bar(30, close=101, high=102, low=100), None) == []
    signals = strategy.on_bar(bar(31, close=103, high=103, low=102), None)
    assert signals[0].reason == "range_breakout"
    assert signals[0].created_at == bar(31).ended_at


def test_vwap_pullback_requires_recovery_above_vwap_and_previous_close():
    strategy = VwapPullbackStrategy(min_trend_bps=Decimal(0), pullback_tolerance_bps=Decimal(20))
    strategy.on_bar(bar(29, close=100, high=101, low=99), None)
    strategy.on_bar(bar(30, close=101, high=102, low=100), None)
    signals = strategy.on_bar(bar(31, close=102, high=103, low=100), None)
    assert signals and signals[0].reason == "vwap_pullback"
