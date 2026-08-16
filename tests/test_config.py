# Tests for the startup config validation (config.py).
import pytest
from pydantic import ValidationError

from config import BrokerSettings, WebSettings, WorkerSettings


def test_web_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("ALTCHA_HMAC_KEY", "a-key")
    monkeypatch.delenv("REPLAY_DB_PATH", raising=False)  # exercise the default
    s = WebSettings()
    assert s.altcha_hmac_key == "a-key"
    assert s.replay_db_path == "altcha_replay.db"  # the default


def test_web_settings_missing_required_raises_clearly(monkeypatch):
    monkeypatch.delenv("ALTCHA_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        WebSettings()
    # the error names the missing variable, which is the whole point
    assert "altcha_hmac_key" in str(exc.value).lower()


def test_broker_settings_load_and_missing(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/0")
    assert BrokerSettings().celery_broker_url == "redis://broker:6379/0"
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    with pytest.raises(ValidationError):
        BrokerSettings()


def test_worker_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("CONTACT_SMTP_USER", raising=False)
    monkeypatch.delenv("CONTACT_SMTP_PASS", raising=False)
    with pytest.raises(ValidationError):
        WorkerSettings()


def test_validation_error_hides_submitted_secret_values(monkeypatch):
    # A secret that IS supplied must never appear in a validation error raised for a
    # different missing field. This locks in the hide_input_in_errors setting.
    monkeypatch.setenv("CONTACT_SMTP_PASS", "SUPERSECRET_VALUE")
    monkeypatch.delenv("CONTACT_SMTP_USER", raising=False)
    with pytest.raises(ValidationError) as exc:
        WorkerSettings()
    assert "SUPERSECRET_VALUE" not in str(exc.value)


def test_broker_password_not_leaked_in_an_unrelated_config_error(monkeypatch):
    # The broker URL carries the Redis password. Even when it's present in the environment
    # as an ignored extra, it must not surface in a validation error for a different field.
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://:BROKER_SECRET_PW@redis:6379/0")
    monkeypatch.delenv("ALTCHA_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        WebSettings()
    assert "BROKER_SECRET_PW" not in str(exc.value)
