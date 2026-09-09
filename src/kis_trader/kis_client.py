from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings


class KisApiError(RuntimeError):
    """Sanitized KIS API failure that never includes credentials."""


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at - timedelta(minutes=1)


class KisClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._token: AccessToken | None = None
        self._token_lock = threading.Lock()
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            timeout=settings.kis.timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> "KisClient":
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
                response.raise_for_status()
                body = response.json()
                token = body["access_token"]
                expires_in = int(body.get("expires_in", 86400))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise KisApiError("KIS authentication failed") from exc
            self._token = AccessToken(
                value=token,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            )
            return token

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
        try:
            response = self._client.get(path, headers=headers, params=params)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KisApiError(f"KIS request failed: {path}") from exc
        if body.get("rt_cd") != "0":
            code = str(body.get("msg_cd", "UNKNOWN"))
            message = str(body.get("msg1", "request rejected"))[:200]
            raise KisApiError(f"KIS rejected request [{code}]: {message}")
        return body

