# ============================================================
# keepbreath.ing — contact API (app.py)
# Author: Mark Thompson
# ============================================================
# Flask endpoint that turns a contact-form POST into an email.


# ------------------------------------------------------------
# IMPORTS & APP SETUP
# Flask, plus the standard-library mail tools.
# ------------------------------------------------------------
import base64
import binascii
import json
import os
import smtplib
import ssl
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import structlog
from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException
from email_validator import validate_email, EmailNotValidError
from altcha import create_challenge, verify_solution

import replay

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = (
    16 * 1024
)  # cap request bodies at 16 KB → 413 if larger


# ------------------------------------------------------------
# CONFIG
# Connection details, credentials from the environment, size limits.
# ------------------------------------------------------------
SMTP_HOST = "mail.keepbreath.ing"
SMTP_PORT = 587
SMTP_USER = os.environ["CONTACT_SMTP_USER"]
SMTP_PASS = os.environ["CONTACT_SMTP_PASS"]

# verifying TLS context for the SMTP send — checks the server cert + hostname,
# using the system CA trust store, and refuses anything older than TLS 1.2
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.minimum_version = ssl.TLSVersion.TLSv1_2

TO_ADDR = "contact@keepbreath.ing"
FROM_ADDR = "contact@keepbreath.ing"


MAX_NAME = 75
MAX_EMAIL = 254
MAX_MESSAGE = 1000

# ALTCHA proof-of-work bot defense. The HMAC key signs challenges so they can't be
# forged. cost = the PoW difficulty — TUNE this against the live widget (too low =
# weak, too high = the visitor waits too long). 5000 is the library's example value.
ALTCHA_HMAC_KEY = os.environ["ALTCHA_HMAC_KEY"]
ALTCHA_COST = 5000  # placeholder — measure + tune on the real round-trip


# ------------------------------------------------------------
# LOGGING
# Structured JSON to stdout; metadata only — never message content or secrets.
# ------------------------------------------------------------
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
log = structlog.get_logger()

# create the replay-registry table on startup (idempotent)
replay.init_db()


@app.before_request
def bind_request_id():
    # fresh request-id per request so every log line in it can be correlated
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))


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
def validate_contact_form(data):
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
# BUILD & SEND
# Build the email safely and send it over authenticated submission.
# ------------------------------------------------------------


def build_email(cleaned):
    msg = EmailMessage()
    msg["From"] = FROM_ADDR
    msg["To"] = TO_ADDR
    msg["Reply-To"] = cleaned["email"]
    msg["Subject"] = f"Contact form: {cleaned['name']}"
    body = (
        f"Name: {cleaned['name']}\n"
        f"Email: {cleaned['email']}\n\n"
        f"Message:\n{cleaned['message']}"
    )
    msg.set_content(body)
    return msg


def send_email(msg):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.starttls(context=SSL_CONTEXT)
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


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
    return jsonify(asdict(challenge))


# ------------------------------------------------------------
# ROUTE — POST /api/contact
# Validate, send, and return a small JSON status.
# ------------------------------------------------------------


@app.route("/api/contact", methods=["POST"])
def contact():
    if not request.is_json:
        return jsonify({"ok": False, "error": "expected json"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400

    # honeypot: a hidden field humans never fill; if it has anything in it, treat
    # it as a bot — return a success-looking response and silently drop (no signal)
    if data.get("website"):
        log.info("honeypot_triggered")
        return jsonify({"ok": True}), 200

    # ALTCHA proof-of-work: the widget's solved token must verify against our HMAC
    # key (signature + solution + not expired). Rejects bots that skip the work.
    # NOTE: untested until the widget round-trip; replay guard = short expiry for
    # now (a shared used-token store is the follow-up).
    altcha_token = data.get("altcha")
    if (
        not altcha_token
        or not verify_solution(
            altcha_token, ALTCHA_HMAC_KEY, check_expires=True
        ).verified
    ):
        log.info("altcha_failed")
        return jsonify({"ok": False, "error": "verification failed"}), 400

    # single-use: a valid token that's already been accepted is a replay. Key the
    # registry on the challenge signature (base64-JSON payload -> "signature").
    # Guard the decode: a malformed token is a rejection, not a 500.
    try:
        signature = json.loads(base64.b64decode(altcha_token))["signature"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        log.info("altcha_malformed")
        return jsonify({"ok": False, "error": "verification failed"}), 400
    reserve_until = int(
        (datetime.now(timezone.utc) + timedelta(minutes=25)).timestamp()
    )
    if not replay.try_reserve(signature, reserve_until):
        log.info("altcha_replay")
        return jsonify({"ok": False, "error": "verification failed"}), 400

    cleaned, err = validate_contact_form(data)
    if err:
        log.info("validation_failed", reason=err)
        return jsonify({"ok": False, "error": err}), 400
    msg = build_email(cleaned)
    try:
        send_email(msg)
    except Exception:
        log.exception("send_failed")
        return jsonify({"ok": False, "error": "failed to send"}), 502
    log.info("message_sent")
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------
# DEV ENTRY POINT
# Local dev only (python app.py); gunicorn runs it in production.
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
