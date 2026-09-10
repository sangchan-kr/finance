from datetime import datetime
from zoneinfo import ZoneInfo

from kis_trader.live_simulation import KisLiveSimulationFeed


class FakeMarket:
    def orderbook(self, _symbol):
        return {"bidp1": "99", "askp1": "101", "bidp_rsqn1": "5", "askp_rsqn1": "7"}

    def intraday_minutes(self, _symbol, before):
        return [
            {"stck_bsop_date": "20260909", "stck_cntg_hour": "100000", "stck_oprc": "100", "stck_hgpr": "102", "stck_lwpr": "98", "stck_prpr": "101", "cntg_vol": "1000"},
            {"stck_bsop_date": "20260909", "stck_cntg_hour": "100100", "stck_oprc": "101", "stck_hgpr": "102", "stck_lwpr": "100", "stck_prpr": "101", "cntg_vol": "500"},
        ]


class FakeEngine:
    def __init__(self):
        self.events = []

    def on_bar(self, event):
        self.events.append(event)
        return True


def test_live_feed_excludes_current_incomplete_minute():
    engine = FakeEngine()
    now = datetime(2026, 9, 9, 10, 1, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    count = KisLiveSimulationFeed(FakeMarket(), engine).poll(["005930"], now)
    assert count == 1
    assert engine.events[0].ended_at.minute == 1
    assert engine.events[0].ask == 101
