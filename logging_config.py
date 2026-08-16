# ============================================================
# keepbreath.ing — contact API (logging_config.py)
# Author: Mark Thompson
# ============================================================
# Shared structured-logging setup, imported by BOTH the web app and the Celery
# worker so the two processes emit the same JSON to stdout. Kept in one place so
# the format can't drift between them. Metadata only — never message content or
# secrets.
import structlog


def configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )
    return structlog.get_logger()
