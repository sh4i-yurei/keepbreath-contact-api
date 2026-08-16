# ============================================================
# keepbreath.ing — contact API (config.py)
# Author: Mark Thompson
# ============================================================
# Typed configuration, read and validated from the environment with pydantic-settings.
# Each process instantiates only the settings it needs, so a missing variable fails loudly
# and clearly at startup instead of much later. This is also how least privilege stays
# honest: the web process never constructs WorkerSettings (never needs the SMTP password),
# and the worker never constructs WebSettings (never needs the ALTCHA key). The one value
# both share — the broker URL — lives in its own BrokerSettings, validated in celery_app,
# which both import.
from pydantic_settings import BaseSettings, SettingsConfigDict

# hide_input_in_errors keeps the submitted values (which include the Redis password embedded
# in the broker URL) out of any validation error message and its logs.
_CONFIG = SettingsConfigDict(case_sensitive=False, extra="ignore", hide_input_in_errors=True)


class BrokerSettings(BaseSettings):
    """The one setting both processes share: where the queue lives. Validated in celery_app,
    so the broker URL is required for both the web app and the worker."""

    model_config = _CONFIG

    celery_broker_url: str


class WebSettings(BaseSettings):
    """Config only the web (Flask) process requires. Field names map to upper-case
    environment variables (ALTCHA_HMAC_KEY, REPLAY_DB_PATH). A field with no default is
    required, so a missing variable raises a clear error the moment the process starts."""

    model_config = _CONFIG

    altcha_hmac_key: str  # signs the ALTCHA proof-of-work challenges
    replay_db_path: str = "altcha_replay.db"  # sensible default; overridden on the droplet


class WorkerSettings(BaseSettings):
    """Config only the Celery worker process requires. Validated when the worker boots, so a
    missing mail credential fails at startup rather than on the first send attempt."""

    model_config = _CONFIG

    contact_smtp_user: str  # the contact@ send-as login
    contact_smtp_pass: str  # its submission password
