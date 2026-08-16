# Test config: run Celery tasks in-process ("eager") so the suite needs no worker, and
# swap the Redis client for an in-memory fake so the dead-letter shelf works without a
# real Redis. Failures are captured on the result object (not re-raised) so tests can
# assert on them.
import fakeredis
import pytest

import tasks
from celery_app import celery


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
