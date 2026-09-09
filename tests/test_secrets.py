from conftest import make_settings

from kis_trader.secrets import Credentials, SecretStore


def test_secret_store_round_trip_and_environment(tmp_path, monkeypatch):
    values = {}
    monkeypatch.setattr(
        "kis_trader.secrets.keyring.set_password",
        lambda service, key, value: values.__setitem__((service, key), value),
    )
    monkeypatch.setattr(
        "kis_trader.secrets.keyring.get_password",
        lambda service, key: values.get((service, key)),
    )
    store = SecretStore("test-service")
    store.save(Credentials("app-key", "app-secret", "12345678"))
    settings = make_settings(tmp_path)
    loaded = store.apply_to_environment(settings)
    assert loaded.account_no == "12345678"
    assert settings.kis.credentials() == ("app-key", "app-secret")

