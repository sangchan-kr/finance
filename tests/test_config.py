import pytest
from conftest import make_settings

from kis_trader.config import ConfigError


def test_market_data_mode_cannot_enable_orders(tmp_path):
    with pytest.raises(ConfigError, match="cannot enable orders"):
        make_settings(tmp_path, order_enabled=True)


def test_secrets_are_environment_variable_names(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    with pytest.raises(ConfigError, match="Set both"):
        settings.kis.credentials()
    monkeypatch.setenv("TEST_KIS_KEY", "key")
    monkeypatch.setenv("TEST_KIS_SECRET", "secret")
    assert settings.kis.credentials() == ("key", "secret")


def test_collection_limit_is_separate_from_trading_limit(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.collection.max_symbols == 40
    assert settings.risk.max_symbols_per_day == 3
