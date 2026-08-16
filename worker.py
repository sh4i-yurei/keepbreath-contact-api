# ============================================================
# keepbreath.ing — contact API (worker.py)
# Author: Mark Thompson
# ============================================================
# The Celery worker's entry point (the compose worker runs `celery -A worker.celery`).
# It validates the worker's environment FIRST — so a missing mail credential or broker URL
# aborts startup here with a clear error, rather than surfacing on the first send — and only
# then exposes the Celery app. The web app never imports this module, so it still never
# needs (or holds) the mail credentials.
from config import WorkerSettings

WorkerSettings()  # raises loudly here, aborting startup, if the worker's env is incomplete

from celery_app import celery  # noqa: E402  (validate the env before touching Celery)

__all__ = ["celery"]
