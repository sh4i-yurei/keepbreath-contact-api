# ============================================================
# keepbreath.ing — contact API (config.py)
# Author: Mark Thompson
# ============================================================
# Typed configuration, read and validated from the environment with pydantic-settings.
# Each process declares only the variables IT needs, so instantiating the class validates
# that process's config and fails loudly and clearly at startup if something is missing —
# instead of a confusing error much later. This also keeps least privilege honest: the web
# process never even asks for the SMTP credentials, and the worker never asks for the
# ALTCHA key.
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """Config the web (Flask) process requires. Field names map to upper-case environment
    variables (ALTCHA_HMAC_KEY, CELERY_BROKER_URL, ...). A field with no default is
    required, so a missing variable raises a clear error the moment the process starts."""

    # hide_input_in_errors keeps the submitted values (which include the Redis password
    # embedded in the broker URL) out of any validation error message and its logs.
    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", hide_input_in_errors=True
    )

    altcha_hmac_key: str  # signs the ALTCHA proof-of-work challenges
    celery_broker_url: str  # where the send is enqueued (the Redis broker)
    replay_db_path: str = "altcha_replay.db"  # sensible default; overridden on the droplet


class WorkerSettings(BaseSettings):
    """Config the Celery worker process requires. Validated when the worker boots, so a
    missing mail credential fails at startup rather than on the first send attempt."""

    # hide_input_in_errors keeps the submitted values (which include the Redis password
    # embedded in the broker URL) out of any validation error message and its logs.
    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", hide_input_in_errors=True
    )

    contact_smtp_user: str  # the contact@ send-as login
    contact_smtp_pass: str  # its submission password
    celery_broker_url: str  # the Redis broker the worker consumes from
