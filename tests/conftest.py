from __future__ import annotations

from pathlib import Path

import yaml

from kis_trader.config import load_settings


def make_settings(tmp_path: Path, **overrides):
    raw = {
        "mode": "market-data-only",
        "market_data_environment": "real",
        "account_environment": "paper",
        "order_enabled": False,
        "kis": {
            "app_key_env": "TEST_KIS_KEY",
            "app_secret_env": "TEST_KIS_SECRET",
            "account_no_env": "TEST_KIS_ACCOUNT",
        },
        "storage": {"database_path": "data/test.db"},
        "risk": {
            "allowed_symbols": ["005930"],
            "max_symbols_per_day": 3,
            "max_order_amount": 1_000_000,
            "max_daily_investment": 3_000_000,
            "max_daily_loss": 100_000,
            "max_spread_bps": 50,
            "stop_file": "STOP_TRADING",
        },
        "live_trading": {
            "unlock_required": True,
            "unlock_env": "TEST_UNLOCK",
            "unlock_value_env": "TEST_UNLOCK_EXPECTED",
            "account_allowlist": [],
        },
    }
    raw.update(overrides)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_settings(path)
