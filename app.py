# ============================================================
# keepbreath.ing — contact API (app.py)
# Author: Mark Thompson
# ============================================================
# Flask endpoint that validates a contact-form POST and hands it to the background
# worker (tasks.py) to email. Validation and anti-bot checks happen here; the send does not.


# ------------------------------------------------------------
# IMPORTS & APP SETUP
# Flask and the request-handling pieces. The mail send lives in the Celery worker
# (tasks.py), so no SMTP libraries are imported here.
# ------------------------------------------------------------
import base64
import binascii
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from flask import Flask, request, jsonify, g
from werkzeug.exceptions import HTTPException
from email_validator import validate_email, EmailNotValidError
from altcha import create_challenge, verify_solution
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

import replay
from config import WebSettings
from logging_config import configure_logging
from tasks import send_contact_email

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # cap request bodies at 16 KB → 413 if larger


# ------------------------------------------------------------
# CONFIG
# Size limits and the ALTCHA key. The mail/SMTP config lives in tasks.py now, since
# the worker — not this web app — does the sending.
# ------------------------------------------------------------
MAX_NAME = 75
MAX_EMAIL = 254
MAX_MESSAGE = 1000

# ALTCHA proof-of-work bot defense. The HMAC key signs challenges so they can't be
# forged. cost = the PoW difficulty — TUNE this against the live widget (too low =
# weak, too high = the visitor waits too long). 5000 is the library's example value.
# WebSettings() validates the whole web config (the ALTCHA key, the broker URL, and the
# replay DB path) right here at startup, raising a clear error if any required variable is
# missing — rather than a confusing failure later.
settings = WebSettings()
ALTCHA_HMAC_KEY = settings.altcha_hmac_key
ALTCHA_COST = 5000  # placeholder — measure + tune on the real round-trip


# ------------------------------------------------------------
# LOGGING
# Structured JSON to stdout (shared config in logging_config.py so the web app and the
# worker log identically); metadata only — never message content or secrets.
# ------------------------------------------------------------
log = configure_logging()

# create the replay-registry table on startup (idempotent)
replay.init_db()


@app.before_request
def bind_request_id():
    # fresh request-id per request so every log line in it can be correlated
    structlog.contextvars.clear_contextvars()
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)
    g.request_id = request_id  # stash it so the enqueue can pass it to the worker
    # start the request timer. perf_counter is a monotonic clock (only ever moves
    # forward, immune to system-clock changes), which is what you want for measuring
    # an elapsed interval. after_request reads this back to log the duration.
    g.request_start = time.perf_counter()


@app.after_request
def log_request_completed(response):
    # One canonical summary line per request — structlog's recommended pattern: a
    # single entry carrying method, path, status, and total duration in seconds.
    # Metadata only (no body, no query content), so no personal data reaches the logs.
    # Duration is in seconds to match Prometheus base-unit convention, so the future
    # metrics work reads the same units without renaming anything.
    # Skip /health: the Docker HEALTHCHECK hits it every 30s, and logging each probe
    # would bury real traffic under thousands of lines a day.
    if request.path != "/health":
        start = getattr(g, "request_start", None)
        log.info(
            "request_completed",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_seconds=(round(time.perf_counter() - start, 3) if start is not None else None),
        )
    return response


# ------------------------------------------------------------
# ERROR HANDLERS
# This is an API — every response is JSON, including errors. Without these,
# Flask returns its HTML error pages (e.g. the 16 KB body cap trips an HTML 413).
# ------------------------------------------------------------
@app.errorhandler(HTTPException)
def handle_http_error(e):
    # covers 400 / 404 / 405 / 413 / etc. — reason as JSON, original status kept.
    return jsonify({"ok": False, "error": e.name}), e.code


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    # anything that isn't already an HTTPException is an unhandled 500; log it
    # (with the request-id) but never leak internals back to the caller.
    log.exception("unhandled_error")
    return jsonify({"ok": False, "error": "internal server error"}), 500


# ------------------------------------------------------------
# HEALTH — liveness probe for the Docker HEALTHCHECK / uptime monitoring.
# Deliberately lightweight: confirms the process is up, doesn't touch SMTP/DB.
# ------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------
# VALIDATION
# Server-side checks on the submitted data (never trust the client).
# ------------------------------------------------------------
def validate_contact_form(data: dict) -> tuple[dict[str, str] | None, str | None]:
    cleaned = {}
    for field in ["name", "email", "message"]:
        value = data.get(field)
        if not isinstance(value, str):
            return None, f"Invalid {field}."
        cleaned[field] = value
    # strip whitespace
    cleaned = {k: v.strip() for k, v in cleaned.items()}

    # basic length checks
    if not cleaned["name"] or len(cleaned["name"]) > MAX_NAME:
        return None, "Invalid name"
    if not cleaned["email"] or len(cleaned["email"]) > MAX_EMAIL:
        return None, "Invalid email"
    if not cleaned["message"] or len(cleaned["message"]) > MAX_MESSAGE:
        return None, "Invalid message"

    # reject CR/LF in name — it lands in the Subject header (injection guard)
    if "\r" in cleaned["name"] or "\n" in cleaned["name"]:
        return None, "Invalid name"

    # email format + safety via the email-validator library
    try:
        result = validate_email(cleaned["email"], check_deliverability=False)
        cleaned["email"] = result.normalized
    except EmailNotValidError:
        return None, "Invalid email"

    return cleaned, None


# ------------------------------------------------------------
# ALTCHA — proof-of-work challenge (bot defense)
# The widget fetches a signed challenge here, solves it in the browser, and sends
# the solution back with the form for the route's verify_solution() to check.
# NOTE: untested until the self-hosted widget + a deploy exist — the exact challenge
# serialization and the cost tuning get confirmed on that live round-trip.
# ------------------------------------------------------------
@app.route("/api/challenge")
def altcha_challenge():
    challenge = create_challenge(
        algorithm="PBKDF2/SHA-256",
        cost=ALTCHA_COST,
        hmac_secret=ALTCHA_HMAC_KEY,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    return jsonify(challenge.to_dict())


# ------------------------------------------------------------
# ROUTE — POST /api/contact
# Validate, enqueue the send, and return a small JSON status.
# ------------------------------------------------------------


@app.route("/api/contact", methods=["POST"])
def contact():
    if not request.is_json:
        return jsonify({"ok": False, "error": "expected json"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400

    # honeypot: a hidden field humans never fill; if it has anything in it, treat it as a
    # bot. Return the SAME response a real accept gives (202, ok:true) so a probing bot
    # can't tell the honeypot apart, and silently drop it (no enqueue).
    if data.get("website"):
        log.info("honeypot_triggered")
        return jsonify({"ok": True}), 202

    # ALTCHA proof-of-work: the widget's solved token must verify against our HMAC key
    # (signature + solution + not expired). Rejects bots that skip the work. The single-use
    # replay guard below is backed by the SQLite registry in replay.py.
    altcha_token = data.get("altcha")
    if not altcha_token or not verify_solution(altcha_token, ALTCHA_HMAC_KEY).verified:
        log.info("altcha_failed")
        return jsonify({"ok": False, "error": "verification failed"}), 400

    # single-use: a valid token that's already been accepted is a replay. Key the
    # registry on the challenge signature (base64-JSON payload -> "signature").
    # Guard the decode: a malformed token is a rejection, not a 500.
    try:
        signature = json.loads(base64.b64decode(altcha_token))["challenge"]["signature"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        log.info("altcha_malformed")
        return jsonify({"ok": False, "error": "verification failed"}), 400
    reserve_until = int((datetime.now(timezone.utc) + timedelta(minutes=25)).timestamp())
    if not replay.try_reserve(signature, reserve_until):
        log.info("altcha_replay")
        return jsonify({"ok": False, "error": "verification failed"}), 400

    cleaned, err = validate_contact_form(data)
    if err:
        log.info("validation_failed", reason=err)
        return jsonify({"ok": False, "error": err}), 400

    # Hand the send to the background worker and return immediately — the request no longer
    # waits on the mail server. Only a broker/queue fault (Redis down) is a real "try again
    # later" here; catch exactly those and return 503. Anything else is an unexpected bug, so
    # let it bubble to the 500 handler rather than hiding it behind a 503.
    try:
        send_contact_email.delay(cleaned, request_id=g.request_id)
    except (KombuOperationalError, RedisError):
        log.exception("enqueue_broker_unavailable")
        return jsonify({"ok": False, "error": "temporarily unavailable"}), 503
    log.info("message_enqueued")
    # 202 Accepted: the request is validated and queued, not yet delivered.
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# DEV ENTRY POINT
# Local dev only (python app.py); gunicorn runs it in production.
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
