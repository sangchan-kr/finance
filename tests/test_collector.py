import json

from conftest import make_settings

from kis_trader.collector import BackgroundCollector
from kis_trader.ledger import Ledger


class FakeClient:
    def __init__(self, _settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get(self, _path, _tr_id, params):
        symbol = params["FID_INPUT_ISCD"]
        return {
            "output": {
                "stck_prpr": "70000" if symbol == "005930" else "80000",
                "prdy_ctrt": "1.25",
                "acml_vol": "123456",
            }
        }


def test_collect_once_saves_sanitized_market_output(tmp_path):
    settings = make_settings(tmp_path)
    ledger = Ledger(settings.storage.database_path)
    ledger.initialize(False)
    snapshots = BackgroundCollector(
        settings,
        ledger,
        ["005930"],
        client_factory=FakeClient,
    ).collect_once()
    assert snapshots[0].price == "70000"
    rows = ledger.recent_snapshots()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "005930"
    with ledger.connect() as connection:
        raw = connection.execute("SELECT raw_json FROM market_snapshots").fetchone()[0]
    assert json.loads(raw)["acml_vol"] == "123456"

