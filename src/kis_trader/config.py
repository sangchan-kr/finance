from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


class ConfigError(ValueError):
    """Raised when configuration is missing or unsafe."""


MarketDataEnvironment = Literal["real", "demo"]
AccountEnvironment = Literal["paper", "live"]


@dataclass(frozen=True)
class KisSettings:
    app_key_env: str
    app_secret_env: str
    account_no_env: str
    product_code: str = "01"
    timeout_seconds: float = 10.0

    def credentials(self) -> tuple[str, str]:
        app_key = os.environ.get(self.app_key_env, "")
        app_secret = os.environ.get(self.app_secret_env, "")
        if not app_key or not app_secret:
            raise ConfigError(
                f"Set both {self.app_key_env} and {self.app_secret_env} environment variables"
            )
        return app_key, app_secret

    def account_no(self) -> str:
        value = os.environ.get(self.account_no_env, "")
        if value and (not value.isdigit() or len(value) != 8):
            raise ConfigError("KIS account number must contain exactly 8 digits")
        return value


@dataclass(frozen=True)
class StorageSettings:
    database_path: Path


@dataclass(frozen=True)
class RiskSettings:
    allowed_symbols: frozenset[str]
    max_symbols_per_day: int
    max_order_amount: int
    max_daily_investment: int
    max_daily_loss: int
    max_spread_bps: int
    stop_file: Path


@dataclass(frozen=True)
class LiveTradingSettings:
    unlock_required: bool
    unlock_env: str
    unlock_value_env: str
    account_allowlist: frozenset[str]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    mode: Literal["market-data-only", "shadow", "trading"]
    market_data_environment: MarketDataEnvironment
    account_environment: AccountEnvironment
    order_enabled: bool
    kis: KisSettings
    storage: StorageSettings
    risk: RiskSettings
    live_trading: LiveTradingSettings

    @property
    def api_base_url(self) -> str:
        if self.market_data_environment == "real":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    def assert_orders_locked(self) -> None:
        if self.mode == "market-data-only" and self.order_enabled:
            raise ConfigError("market-data-only mode cannot enable orders")
        if self.account_environment == "live" and self.order_enabled:
            account = self.kis.account_no()
            live = self.live_trading
            expected = os.environ.get(live.unlock_value_env, "")
            supplied = os.environ.get(live.unlock_env, "")
            if live.unlock_required and (not expected or supplied != expected):
                raise ConfigError("live trading unlock is missing or incorrect")
            if account not in live.account_allowlist:
                raise ConfigError("live account is not allowlisted")


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"Missing configuration key: {key}")
    return mapping[key]


def _market_data_environment(value: Any) -> MarketDataEnvironment:
    if value not in {"real", "demo"}:
        raise ConfigError("market_data_environment must be 'real' or 'demo'")
    return value


def _account_environment(value: Any) -> AccountEnvironment:
    if value not in {"paper", "live"}:
        raise ConfigError("account_environment must be 'paper' or 'live'")
    return value


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load settings file: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Settings root must be a mapping")

    root = config_path.parent.parent
    kis = _required(raw, "kis")
    storage = _required(raw, "storage")
    risk = _required(raw, "risk")
    live = _required(raw, "live_trading")
    mode = raw.get("mode", "market-data-only")
    if mode not in {"market-data-only", "shadow", "trading"}:
        raise ConfigError("mode must be market-data-only, shadow, or trading")

    settings = Settings(
        project_root=root,
        mode=mode,
        market_data_environment=_market_data_environment(
            raw.get("market_data_environment", "real")
        ),
        account_environment=_account_environment(raw.get("account_environment", "paper")),
        order_enabled=bool(raw.get("order_enabled", False)),
        kis=KisSettings(
            app_key_env=str(_required(kis, "app_key_env")),
            app_secret_env=str(_required(kis, "app_secret_env")),
            account_no_env=str(_required(kis, "account_no_env")),
            product_code=str(kis.get("product_code", "01")),
            timeout_seconds=float(kis.get("timeout_seconds", 10)),
        ),
        storage=StorageSettings(database_path=root / _required(storage, "database_path")),
        risk=RiskSettings(
            allowed_symbols=frozenset(str(x) for x in risk.get("allowed_symbols", [])),
            max_symbols_per_day=int(risk.get("max_symbols_per_day", 3)),
            max_order_amount=int(risk.get("max_order_amount", 0)),
            max_daily_investment=int(risk.get("max_daily_investment", 0)),
            max_daily_loss=int(risk.get("max_daily_loss", 0)),
            max_spread_bps=int(risk.get("max_spread_bps", 50)),
            stop_file=root / risk.get("stop_file", "STOP_TRADING"),
        ),
        live_trading=LiveTradingSettings(
            unlock_required=bool(live.get("unlock_required", True)),
            unlock_env=str(live.get("unlock_env", "LIVE_TRADING_UNLOCK")),
            unlock_value_env=str(
                live.get("unlock_value_env", "LIVE_TRADING_UNLOCK_EXPECTED")
            ),
            account_allowlist=frozenset(str(x) for x in live.get("account_allowlist", [])),
        ),
    )
    settings.assert_orders_locked()
    return settings
