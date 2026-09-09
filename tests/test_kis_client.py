from datetime import UTC, datetime, timedelta

import httpx
from conftest import make_settings

from kis_trader.kis_client import AccessToken, KisApiError, KisClient


def test_cached_token_avoids_token_request(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("TEST_KIS_KEY", "app-key")
    monkeypatch.setenv("TEST_KIS_SECRET", "app-secret")
    cached = AccessToken("cached-token", datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(KisClient, "_load_cached_token", lambda self, _key: cached)

    def handler(request):
        assert request.headers["authorization"] == "Bearer cached-token"
        assert request.url.path.endswith("inquire-price")
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "70000"}})

    client = KisClient(settings, transport=httpx.MockTransport(handler))
    body = client.get("/uapi/domestic-stock/v1/quotations/inquire-price", "TR", {})
    assert body["output"]["stck_prpr"] == "70000"
    client.close()


def test_http_error_includes_sanitized_kis_code(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("TEST_KIS_KEY", "app-key")
    monkeypatch.setenv("TEST_KIS_SECRET", "app-secret")
    cached = AccessToken("cached-token", datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(KisClient, "_load_cached_token", lambda self, _key: cached)

    def handler(_request):
        return httpx.Response(403, json={"msg_cd": "EGW00133", "msg1": "token rate limited"})

    client = KisClient(settings, transport=httpx.MockTransport(handler))
    try:
        client.get("/test", "TR", {})
    except KisApiError as exc:
        assert "EGW00133" in str(exc)
        assert "cached-token" not in str(exc)
    else:
        raise AssertionError("expected KisApiError")
    client.close()
