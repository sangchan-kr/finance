from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from kis_trader.ledger import Ledger
from kis_trader.replay import replay_csv
from kis_trader.simulation import SimulationEngine
from kis_trader.virtual_broker import BrokerConfig, VirtualBroker


class NoSignal:
    strategy_id = "none"
    version = "1"

    def on_bar(self, _bar, _position):
        return []


def test_replay_uses_engine_and_is_idempotent(tmp_path):
    kst = ZoneInfo("Asia/Seoul")
    end = datetime(2026, 9, 9, 9, 1, tzinfo=kst)
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "symbol,started_at,ended_at,open,high,low,close,volume,bid,ask,bid_quantity,ask_quantity,received_at\n"
        f"005930,{(end-timedelta(minutes=1)).isoformat()},{end.isoformat()},100,101,99,100,1000,99,101,10,10,{end.isoformat()}\n",
        encoding="utf-8",
    )
    ledger = Ledger(tmp_path / "db.sqlite")
    ledger.initialize(False)
    broker = VirtualBroker(
        ledger,
        BrokerConfig(Decimal(10000), Decimal(1000), Decimal(10), Decimal("0.25"), Decimal(0), Decimal(0), Decimal(10)),
    )
    engine = SimulationEngine(ledger, broker, [NoSignal()])
    assert replay_csv(csv_path, engine) == 1
    assert replay_csv(csv_path, engine) == 0
