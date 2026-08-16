# ============================================================
# keepbreath.ing — contact API (celery_app.py)
# Author: Mark Thompson
# ============================================================
# The Celery application. Celery is the background-task system that takes the mail
# send off the web request: the web app enqueues a task here and returns right away,
# while a separate worker process actually sends the mail. This module defines the
# Celery app and its behaviour; the task itself lives in tasks.py.
import os

from celery import Celery

from config import BrokerSettings

# The "broker" is the queue that holds pending tasks between the web app (which puts tasks
# in) and the worker (which takes them out). We use Redis for it. BrokerSettings validates
# that CELERY_BROKER_URL is set — for BOTH processes, since both import this module — and
# fails loudly right here if it isn't, with the embedded password kept out of the error.
BROKER_URL = BrokerSettings().celery_broker_url

# How often the scheduled sweep re-drives the dead-letter shelf, in seconds. This is an
# optional operational knob with a sensible default, so it stays a plain environment read
# rather than required config. The live test overrides it to a few seconds.
REDRIVE_INTERVAL_SECONDS = float(os.environ.get("REDRIVE_INTERVAL_SECONDS", "3600"))

# include=["tasks"] makes the worker import tasks.py on startup so the task is registered.
# Without it the worker boots but rejects the job as an "unregistered task".
celery = Celery("contact_api", broker=BROKER_URL, include=["tasks"])

celery.conf.update(
    # Fire-and-forget: we don't need to store or look up task return values, so we
    # skip a result backend entirely. One less thing to run and secure.
    task_ignore_result=True,
    # Acknowledge a task only AFTER it finishes, not when it's picked up. If a worker
    # crashes mid-send, the task is redelivered and run again instead of being lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Take one task at a time rather than grabbing a batch, so a crashing worker can't
    # drag a whole prefetched batch down with it.
    worker_prefetch_multiplier=1,
    # If Redis isn't up yet when the worker boots, keep retrying the connection instead
    # of crashing (Celery 5.3+ made this opt-in).
    broker_connection_retry_on_startup=True,
    # Tasks carry plain JSON, never pickled Python objects — safer across the wire.
    task_serializer="json",
    accept_content=["json"],
    # Schedule for Celery Beat (run inside the worker via --beat): sweep the dead-letter
    # shelf on an interval and re-drive anything on it.
    beat_schedule={
        "redrive-dead-letters": {
            "task": "tasks.redrive_dead_letters",
            "schedule": REDRIVE_INTERVAL_SECONDS,
        },
    },
)
