from __future__ import annotations

import os
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

from .config import ConfigError, Settings

SERVICE_NAME = "kis-intraday-trader"


class SecretStoreError(RuntimeError):
    """Raised when the operating-system credential store is unavailable."""


@dataclass(frozen=True)
class Credentials:
    app_key: str
    app_secret: str
    account_no: str = ""

    def validate(self) -> None:
        if not self.app_key.strip() or not self.app_secret.strip():
            raise ConfigError("App Key and App Secret are required")
        if self.account_no and (not self.account_no.isdigit() or len(self.account_no) != 8):
            raise ConfigError("Account number must contain exactly 8 digits")


class SecretStore:
    def __init__(self, service_name: str = SERVICE_NAME):
        self.service_name = service_name

    def save(self, credentials: Credentials) -> None:
        credentials.validate()
        try:
            keyring.set_password(self.service_name, "app_key", credentials.app_key.strip())
            keyring.set_password(self.service_name, "app_secret", credentials.app_secret.strip())
            keyring.set_password(self.service_name, "account_no", credentials.account_no.strip())
        except KeyringError as exc:
            raise SecretStoreError("Cannot save credentials in the OS credential store") from exc

    def load(self) -> Credentials | None:
        try:
            app_key = keyring.get_password(self.service_name, "app_key") or ""
            app_secret = keyring.get_password(self.service_name, "app_secret") or ""
            account_no = keyring.get_password(self.service_name, "account_no") or ""
        except KeyringError as exc:
            raise SecretStoreError("Cannot read credentials from the OS credential store") from exc
        if not app_key and not app_secret and not account_no:
            return None
        credentials = Credentials(app_key, app_secret, account_no)
        credentials.validate()
        return credentials

    def apply_to_environment(self, settings: Settings) -> Credentials:
        credentials = self.load()
        if credentials is None:
            raise ConfigError("No saved KIS credentials")
        os.environ[settings.kis.app_key_env] = credentials.app_key
        os.environ[settings.kis.app_secret_env] = credentials.app_secret
        if credentials.account_no:
            os.environ[settings.kis.account_no_env] = credentials.account_no
        return credentials

