# ============================================================
# keepbreath.ing — contact API (tasks.py)
# Author: Mark Thompson
# ============================================================
# The background mail-send task, its retry policy, and the self-healing dead-letter
# shelf. This runs in the Celery WORKER, never in the web request.
#
# The delivery model, in one paragraph: every contact message goes to one fixed address
# we control, so no message is ever permanently undeliverable — a failure is always
# either a brief outage or our own misconfiguration, both of which clear. So a send that
# fails fast is retried a few times; anything still not delivered is parked on a Redis
# "shelf" and re-driven on a schedule until it lands. The only message ever truly dropped
# is one that has sat on the shelf past a long age backstop, and that drop is loud.
import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

import redis
import structlog

from celery_app import celery
from logging_config import configure_logging

log = configure_logging()

# Mail endpoint. Credentials are read at SEND time (inside send_email), NOT at import, so
# the web app can import this module to enqueue tasks without ever holding the SMTP
# password. Only the worker, when it actually sends, reads them.
SMTP_HOST = "mail.keepbreath.ing"
SMTP_PORT = 587
TO_ADDR = "contact@keepbreath.ing"
FROM_ADDR = "contact@keepbreath.ing"

# The dead-letter shelf: a Redis list of sends the fast retries didn't deliver. The
# scheduled sweep (redrive_dead_letters) re-sends them until they land.
DEADLETTER_KEY = "contact:deadletters"
# While the sweep is acting on an entry it lives here, so a crash mid-sweep leaves it
# recoverable (moved back on the next sweep) rather than lost.
PROCESSING_KEY = "contact:deadletters:processing"
# How long a message may sit on the shelf before we finally give up on it, loudly. Long
# on purpose — a mail outage or a config fix should happen well within this. Tunable.
MAX_SHELF_AGE_SECONDS = int(
    os.environ.get("DEADLETTER_MAX_AGE_SECONDS", str(3 * 86400))
)


class TransientSendError(Exception):
    """A failure worth burning a FAST retry on: a network blip, dropped connection, 4xx."""


def _redis():
    # A plain Redis client on the same broker Celery uses, for the dead-letter shelf.
    return redis.from_url(
        os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    )


def _ssl_context():
    # Verifying TLS for the send: checks the server cert and hostname against the system
    # trust store and refuses anything older than TLS 1.2.
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def build_email(cleaned):
    # Defense in depth: the web app already validated this, but the worker trusts whatever
    # is on the queue, so re-reject CR/LF in the fields that land in headers. EmailMessage
    # is a second backstop, but this fails fast and clearly.
    for field in ("name", "email"):
        if "\r" in cleaned[field] or "\n" in cleaned[field]:
            raise ValueError(f"newline in {field}")
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
    # Credentials read here, at send time, so they live only in the worker's environment.
    user = os.environ["CONTACT_SMTP_USER"]
    password = os.environ["CONTACT_SMTP_PASS"]
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.starttls(context=_ssl_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def is_transient(exc):
    """Decide whether a failure is worth a FAST retry, or should go straight to the shelf.

    This does NOT decide whether to give up — nothing is ever given up on here, because
    every message goes to our own fixed address and delivers once the problem clears. It
    only picks "5 quick retries now" versus "straight to the slow shelf." Worth a fast
    retry: any OS-level network error (connection refused/reset, timeout, DNS failure, no
    route, TLS hiccup) or a 4xx "try later" SMTP reply. Not worth it (won't clear in 30s):
    bad auth, a 5xx, a bad config — those skip the fast retries and go to the shelf.
    """
    # Order matters: smtplib.SMTPException is itself an OSError subclass, so the SMTP cases
    # MUST be decided before the generic OSError catch, or every SMTP error would wrongly
    # look transient.
    #
    # Connection-level SMTP failures carry no useful reply code — always worth a fast retry.
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return True
    # SMTP replies carrying a status code: 4xx is transient, 5xx is not-fast-retryable.
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    # Any other SMTP-level error (e.g. recipients refused) is a config problem, not a blip.
    if isinstance(exc, smtplib.SMTPException):
        return False
    # Non-SMTP OS/network errors: connection refused/reset, timeout, DNS failure (gaierror),
    # no route, TLS hiccup. OSError is the parent of all of these.
    if isinstance(exc, OSError):
        return True
    return False


def _shelve(data, request_id, reason, first_failed_at, redrive_count):
    """Park a failed send on the Redis shelf for the scheduled sweep to re-drive."""
    entry = json.dumps(
        {
            "data": data,
            "request_id": request_id,
            "reason": reason,
            "first_failed_at": first_failed_at or time.time(),
            "redrive_count": redrive_count,
        }
    )
    try:
        _redis().rpush(DEADLETTER_KEY, entry)
    except Exception as exc:
        # If we can't even shelve it, this is the one place a message can be lost — shout,
        # with enough context to find it, and re-raise so the failure isn't swallowed.
        log.critical(
            "dead_letter_shelve_failed",
            request_id=request_id,
            reason=reason,
            redrive_count=redrive_count,
            error=str(exc),
        )
        raise


@celery.task(
    bind=True,
    autoretry_for=(TransientSendError,),
    max_retries=5,
    retry_backoff=True,  # delays grow 1s, 2s, 4s, 8s, 16s ... (exponential)
    retry_backoff_max=600,  # but never wait more than 10 minutes between attempts
    retry_jitter=True,  # randomize each delay so many queued sends don't retry in lockstep
)
def send_contact_email(
    self, data, request_id=None, first_failed_at=None, redrive_count=0
):
    # Carry the web request's id (and this task's id) onto every log line, so the enqueue
    # in the web logs and the send here can be tied together.
    structlog.contextvars.clear_contextvars()
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    structlog.contextvars.bind_contextvars(task_id=self.request.id)

    # A message that can't even be built is malformed and can never send (only reachable
    # if bad data got onto the queue, since the web app validates first). Drop it loudly
    # rather than re-driving it forever.
    try:
        msg = build_email(data)
    except Exception as exc:
        log.error("build_failed_dropped", error=str(exc), request_id=request_id)
        return

    try:
        send_email(msg)
    except Exception as exc:
        # Fast layer: a transient failure with attempts left gets a quick retry.
        if is_transient(exc) and self.request.retries < self.max_retries:
            log.warning(
                "send_transient_retry", attempt=self.request.retries, error=str(exc)
            )
            raise TransientSendError(str(exc)) from exc
        # Otherwise park it on the shelf for the scheduled sweep. Nothing is abandoned
        # here — the sweep keeps re-driving it until it delivers or the age backstop trips.
        reason = "retry_exhausted" if is_transient(exc) else "not_fast_retryable"
        _shelve(data, request_id, reason, first_failed_at, redrive_count)
        log.warning(
            "send_shelved", reason=reason, error=str(exc), redrive_count=redrive_count
        )
        return  # handled: safely shelved, so the task itself succeeds

    log.info("message_sent", attempt=self.request.retries, redrive_count=redrive_count)


# If one sweep re-drives more than this, something is wrong (a runaway loop) — warn.
REDRIVE_RUNAWAY_WARN = int(os.environ.get("REDRIVE_RUNAWAY_WARN", "100"))


@celery.task
def redrive_dead_letters():
    """Scheduled sweep: re-send everything on the shelf. A message that fails again is
    re-shelved with its ORIGINAL first-failed time, so its age clock keeps running. The
    only messages dropped are those past the age backstop, and that drop is loud."""
    r = _redis()
    redriven = 0
    expired = 0
    try:
        # Recover anything a previous crashed sweep left mid-flight in the processing list.
        while r.lmove(PROCESSING_KEY, DEADLETTER_KEY, "LEFT", "RIGHT"):
            pass
        while True:
            # Atomically move an entry to the processing list before acting on it. If we
            # crash now, it's parked there and the next sweep's recovery step moves it back,
            # so a message is never lost, at worst re-driven twice.
            raw = r.lmove(DEADLETTER_KEY, PROCESSING_KEY, "LEFT", "RIGHT")
            if raw is None:
                break
            entry = json.loads(raw)
            age = time.time() - entry["first_failed_at"]
            if age > MAX_SHELF_AGE_SECONDS:
                log.error(
                    "dead_letter_expired",
                    request_id=entry.get("request_id"),
                    age_days=round(age / 86400, 1),
                    redrive_count=entry.get("redrive_count", 0),
                )
                expired += 1
            else:
                send_contact_email.delay(
                    entry["data"],
                    request_id=entry.get("request_id"),
                    first_failed_at=entry["first_failed_at"],
                    redrive_count=entry.get("redrive_count", 0) + 1,
                )
                redriven += 1
            # lrem is given the exact value lmove returned, so the stored representation
            # matches by construction (one client, no bytes/str mismatch to orphan entries).
            r.lrem(PROCESSING_KEY, 1, raw)
    except redis.exceptions.RedisError:
        # Redis flaked mid-sweep. Anything moved to processing is recovered on the next
        # sweep, so bail cleanly instead of crashing the scheduled task.
        log.exception("redrive_redis_error")
        return
    if redriven > REDRIVE_RUNAWAY_WARN:
        log.warning("redrive_runaway", redriven=redriven)
    if redriven or expired:
        log.info("redrive_swept", redriven=redriven, expired=expired)
