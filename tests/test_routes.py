# Tests for the HTTP routes, driven through Flask's test client.
import app as app_module
from altcha import Challenge, Payload, solve_challenge

VALID = {"name": "Ada Lovelace", "email": "ada@example.com", "message": "hello there"}


def _solved_token(client) -> str:
    # Fetch a challenge from the app's own endpoint and solve it, the way the widget does.
    challenge = Challenge.from_dict(client.get("/api/challenge").get_json())
    solution = solve_challenge(challenge)
    assert solution is not None
    return Payload(challenge, solution).to_base64()


def _no_send(monkeypatch) -> list:
    # Replace the enqueue with a no-op that records calls, so no real task runs.
    calls: list = []
    monkeypatch.setattr(app_module.send_contact_email, "delay", lambda *a, **k: calls.append(a))
    return calls


def test_challenge_endpoint_returns_a_challenge(client):
    resp = client.get("/api/challenge")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "signature" in body and "parameters" in body


def test_valid_submission_returns_202_and_enqueues(client, fresh_replay, monkeypatch):
    calls = _no_send(monkeypatch)
    resp = client.post("/api/contact", json={**VALID, "altcha": _solved_token(client)})
    assert resp.status_code == 202
    assert resp.get_json()["ok"] is True
    assert len(calls) == 1


def test_replayed_token_is_rejected(client, fresh_replay, monkeypatch):
    _no_send(monkeypatch)
    token = _solved_token(client)
    first = client.post("/api/contact", json={**VALID, "altcha": token})
    second = client.post("/api/contact", json={**VALID, "altcha": token})
    assert first.status_code == 202
    assert second.status_code == 400


def test_malformed_token_is_rejected(client):
    resp = client.post("/api/contact", json={**VALID, "altcha": "!!!not-base64!!!"})
    assert resp.status_code == 400


def test_missing_token_is_rejected(client):
    resp = client.post("/api/contact", json={**VALID})
    assert resp.status_code == 400


def test_honeypot_looks_accepted_but_does_not_enqueue(client, monkeypatch):
    calls = _no_send(monkeypatch)
    resp = client.post("/api/contact", json={**VALID, "website": "i-am-a-bot", "altcha": "x"})
    assert resp.status_code == 202  # indistinguishable from a real accept
    assert calls == []  # but nothing was enqueued


def test_validation_failure_is_rejected(client, fresh_replay, monkeypatch):
    _no_send(monkeypatch)
    bad = {**VALID, "message": "", "altcha": _solved_token(client)}
    resp = client.post("/api/contact", json=bad)
    assert resp.status_code == 400
