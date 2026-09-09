from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import httpx
import keyring
from keyring.errors import KeyringError

from .config import Settings


class KisApiError(RuntimeError):
    """Sanitized KIS API failure that never includes credentials."""


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return datetime.now(UTC) < self.expires_at - timedelta(minutes=1)


class KisClient:
    _request_lock = threading.Lock()
    _last_request_at: dict[str, float] = {"real": 0.0, "demo": 0.0}

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._token: AccessToken | None = None
        self._token_lock = threading.Lock()
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            timeout=settings.kis.timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _access_token(self) -> str:
        with self._token_lock:
            if self._token and self._token.is_valid():
                return self._token.value
            app_key, app_secret = self.settings.kis.credentials()
            cached = self._load_cached_token(app_key)
            if cached and cached.is_valid():
                self._token = cached
                return cached.value
            try:
                response = self._client.post(
                    "/oauth2/tokenP",
                    headers={"content-type": "application/json"},
                    json={
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    },
                )
                body = response.json()
                if response.is_error:
                    raise KisApiError(
                        self._error_message(response.status_code, body, "authentication")
                    )
                token = body["access_token"]
                expires_in = int(body.get("expires_in", 86400))
            except KisApiError:
                raise
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise KisApiError("KIS authentication failed") from exc
            self._token = AccessToken(
                value=token,
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            )
            self._save_cached_token(app_key, self._token)
            return token

    def _token_cache_key(self, app_key: str) -> str:
        fingerprint = hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:16]
        return f"access_token_{self.settings.market_data_environment}_{fingerprint}"

    def _load_cached_token(self, app_key: str) -> AccessToken | None:
        try:
            raw = keyring.get_password("kis-intraday-trader", self._token_cache_key(app_key))
            if not raw:
                return None
            body = json.loads(raw)
            return AccessToken(
                value=str(body["value"]),
                expires_at=datetime.fromisoformat(str(body["expires_at"])),
            )
        except (KeyringError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save_cached_token(self, app_key: str, token: AccessToken) -> None:
        try:
            keyring.set_password(
                "kis-intraday-trader",
                self._token_cache_key(app_key),
                json.dumps({"value": token.value, "expires_at": token.expires_at.isoformat()}),
            )
        except KeyringError:
            pass

    def _pace_request(self) -> None:
        environment = self.settings.market_data_environment
        minimum_interval = 1.05 if environment == "demo" else 0.25
        with self._request_lock:
            now = time.monotonic()
            delay = minimum_interval - (now - self._last_request_at[environment])
            if delay > 0:
                time.sleep(delay)
            self._last_request_at[environment] = time.monotonic()

    @staticmethod
    def _error_message(status_code: int, body: Any, operation: str) -> str:
        if isinstance(body, dict):
            code = str(body.get("msg_cd", "UNKNOWN"))[:40]
            message = str(body.get("msg1", "request rejected"))[:200]
            return f"KIS {operation} failed (HTTP {status_code}, {code}): {message}"
        return f"KIS {operation} failed (HTTP {status_code})"

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        app_key, app_secret = self.settings.kis.credentials()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        for attempt in range(3):
            self._pace_request()
            try:
                response = self._client.get(path, headers=headers, params=params)
                try:
                    body = response.json()
                except ValueError:
                    body = None
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise KisApiError(f"KIS request failed: {path}") from exc
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.is_error:
                raise KisApiError(self._error_message(response.status_code, body, "request"))
            if not isinstance(body, dict):
                raise KisApiError("KIS returned an invalid response")
            if body.get("rt_cd") != "0":
                code = str(body.get("msg_cd", "UNKNOWN"))[:40]
                message = str(body.get("msg1", "request rejected"))[:200]
                raise KisApiError(f"KIS rejected request [{code}]: {message}")
            return body
        raise KisApiError(f"KIS request failed after retries: {path}")
