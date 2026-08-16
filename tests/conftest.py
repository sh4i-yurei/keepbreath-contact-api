# Test configuration and shared fixtures.
#
# The required config is set here, before any app module is imported, because those
# modules now validate their environment at import time (config.py). Celery tasks run
# in-process ("eager"), and the Redis client is swapped for an in-memory fake, so the
# suite needs no worker and no real Redis.
import os
import tempfile

os.environ.setdefault("ALTCHA_HMAC_KEY", "test-hmac-key-not-a-secret")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CONTACT_SMTP_USER", "contact@example.com")
os.environ.setdefault("CONTACT_SMTP_PASS", "test-pass")
os.environ.setdefault("REPLAY_DB_PATH", os.path.join(tempfile.gettempdir(), "kb_test_replay.db"))

import fakeredis  # noqa: E402  (imports must follow the env setup above)
import pytest  # noqa: E402

import replay  # noqa: E402
import tasks  # noqa: E402
from celery_app import celery  # noqa: E402


@pytest.fixture(autouse=True)
def _eager_celery():
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = False
    yield


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    # One in-memory Redis per test, wired in wherever tasks.py reaches for a client.
    fr = fakeredis.FakeRedis()
    monkeypatch.setattr(tasks, "_redis", lambda: fr)
    return fr


@pytest.fixture()
def fresh_replay(tmp_path, monkeypatch):
    # An isolated, empty replay database per test.
    db = str(tmp_path / "replay.db")
    monkeypatch.setattr(replay, "DB_PATH", db)
    replay.init_db()
    return db


@pytest.fixture()
def client():
    # Flask's test client round-trips requests without a live server.
    import app as app_module

    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()
