from datetime import date

from kis_trader.market_data import MarketData


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, tr_id, params):
        self.calls.append((path, tr_id, params))
        return {"output": {"stck_prpr": "70000"}, "output2": [{"stck_cntg_hour": "091000"}]}


def test_current_price_uses_official_endpoint():
    client = FakeClient()
    assert MarketData(client).current_price("005930")["stck_prpr"] == "70000"
    assert client.calls[0][1] == "FHKST01010100"


def test_historical_minutes_formats_date():
    client = FakeClient()
    result = MarketData(client).historical_minutes("005930", date(2026, 9, 9))
    assert result[0]["stck_cntg_hour"] == "091000"
    assert client.calls[0][2]["FID_INPUT_DATE_1"] == "20260909"


def test_invalid_symbol_is_rejected_before_api_call():
    client = FakeClient()
    try:
        MarketData(client).current_price("5930")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    assert not client.calls

