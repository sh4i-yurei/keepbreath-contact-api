# Tests for the startup config validation (config.py).
import pytest
from pydantic import ValidationError

from config import WebSettings, WorkerSettings


def test_web_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("ALTCHA_HMAC_KEY", "a-key")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.delenv("REPLAY_DB_PATH", raising=False)  # exercise the default
    s = WebSettings()
    assert s.altcha_hmac_key == "a-key"
    assert s.celery_broker_url == "redis://broker:6379/0"
    assert s.replay_db_path == "altcha_replay.db"  # the default


def test_web_settings_missing_required_raises_clearly(monkeypatch):
    monkeypatch.delenv("ALTCHA_HMAC_KEY", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        WebSettings()
    # the error names the missing variable, which is the whole point
    assert "altcha_hmac_key" in str(exc.value).lower()


def test_worker_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("CONTACT_SMTP_USER", raising=False)
    monkeypatch.delenv("CONTACT_SMTP_PASS", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    with pytest.raises(ValidationError):
        WorkerSettings()
