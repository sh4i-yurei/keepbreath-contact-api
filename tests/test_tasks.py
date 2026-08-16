# Tests for the background mail task and the self-healing dead-letter shelf.
import smtplib
import socket
import ssl
import time

import pytest

import tasks
from tasks import (
    DEADLETTER_KEY,
    build_email,
    is_transient,
    redrive_dead_letters,
    send_contact_email,
)

VALID = {"name": "Ada Lovelace", "email": "ada@example.com", "message": "hello there"}


# ------------------------------------------------------------
# is_transient — fast-retry vs straight-to-shelf (never "give up")
# ------------------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        smtplib.SMTPServerDisconnected("connection dropped"),
        smtplib.SMTPConnectError(421, "cannot connect"),
        ssl.SSLError("tls hiccup"),
        TimeoutError("timed out"),
        ConnectionResetError("reset by peer"),
        socket.gaierror("name resolution failed"),  # DNS — the H2 gap
        OSError("[Errno 113] No route to host"),  # network unreachable — the H2 gap
        smtplib.SMTPResponseException(451, "temporary local error"),  # 4xx
    ],
)
def test_transient_errors_get_fast_retry(exc):
    assert is_transient(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        smtplib.SMTPAuthenticationError(535, "bad credentials"),
        smtplib.SMTPSenderRefused(550, b"bad sender", "contact@keepbreath.ing"),
        smtplib.SMTPResponseException(550, "mailbox unavailable"),  # 5xx
        smtplib.SMTPRecipientsRefused({"x@y.z": (550, b"no such user")}),
        ValueError("malformed data"),
    ],
)
def test_non_fast_retryable_errors_skip_to_shelf(exc):
    assert is_transient(exc) is False


# ------------------------------------------------------------
# build_email — safe construction + CRLF re-check (defense in depth)
# ------------------------------------------------------------
def test_build_email_headers_and_body():
    msg = build_email(VALID)
    assert msg["From"] == "contact@keepbreath.ing"
    assert msg["To"] == "contact@keepbreath.ing"
    assert msg["Reply-To"] == "ada@example.com"
    assert "Ada Lovelace" in msg["Subject"]
    body = msg.get_content()
    assert "hello there" in body


def test_build_email_rejects_crlf_in_name():
    with pytest.raises(ValueError):
        build_email({**VALID, "name": "Ada\r\nBcc: victim@example.com"})


# ------------------------------------------------------------
# the task — deliver, shelve, and drop-malformed
# ------------------------------------------------------------
def test_task_success_delivers_and_leaves_shelf_empty(monkeypatch, fake_redis):
    sent = []
    monkeypatch.setattr(tasks, "send_email", lambda msg: sent.append(msg))
    result = send_contact_email.apply(args=[VALID])
    assert result.successful()
    assert len(sent) == 1
    assert fake_redis.llen(DEADLETTER_KEY) == 0


def test_task_shelves_when_not_fast_retryable(monkeypatch, fake_redis):
    def refuse(_msg):
        raise smtplib.SMTPAuthenticationError(535, "bad credentials")

    monkeypatch.setattr(tasks, "send_email", refuse)
    result = send_contact_email.apply(args=[VALID])
    assert result.successful()  # handled by shelving, not a task failure
    assert fake_redis.llen(DEADLETTER_KEY) == 1  # the message is safe on the shelf


def test_task_drops_unbuildable_message(monkeypatch, fake_redis):
    # A CRLF-injected name can't build; it should be dropped, not shelved for re-drive.
    bad = {**VALID, "name": "x\r\nBcc: e@vil"}
    result = send_contact_email.apply(args=[bad])
    assert result.successful()
    assert fake_redis.llen(DEADLETTER_KEY) == 0


# ------------------------------------------------------------
# the sweep — re-drive young messages, expire old ones
# ------------------------------------------------------------
def test_redrive_resends_a_young_shelved_message(monkeypatch, fake_redis):
    tasks._shelve(VALID, "req-1", "retry_exhausted", time.time(), 0)  # just failed
    sent = []
    monkeypatch.setattr(tasks, "send_email", lambda msg: sent.append(msg))
    redrive_dead_letters.apply()
    assert len(sent) == 1  # the sweep re-sent it
    assert fake_redis.llen(DEADLETTER_KEY) == 0  # delivered, so it left the shelf


def test_redrive_expires_and_drops_an_old_message(monkeypatch, fake_redis):
    old = time.time() - (4 * 86400)  # 4 days — past the 3-day backstop
    tasks._shelve(VALID, "req-2", "retry_exhausted", old, 12)
    sent = []
    monkeypatch.setattr(tasks, "send_email", lambda msg: sent.append(msg))
    redrive_dead_letters.apply()
    assert len(sent) == 0  # NOT re-sent — it's too old
    assert fake_redis.llen(DEADLETTER_KEY) == 0  # dropped off the shelf
