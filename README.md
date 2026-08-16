# keepbreath-contact-api

Contact-form backend for [keepbreath.ing](https://keepbreath.ing) — a small Flask API
(`POST /api/contact`) that turns a form submission into an email so a visitor can reach me
and I can reply as `contact@keepbreath.ing`, without ever exposing a real inbox address.

## What it does

Receives the JSON form POST, validates it server-side (required fields, length caps, an
RFC-aware email check, and a CRLF header-injection guard), runs three anti-bot layers
(honeypot, invisible ALTCHA proof-of-work, nginx rate limiting), and then **hands the send to
a background worker and returns immediately**. The worker builds the message with
`email.message.EmailMessage` and delivers it, retrying transient failures. `From:` is
`contact@keepbreath.ing` and `Reply-To:` is the visitor — hitting reply goes to them; my Gmail
is never shown.

## Architecture — the send runs off the request path

The mail send is slow and can fail, so it does **not** happen inside the web request. Instead
the request drops the message onto a queue and answers the visitor right away with `202
Accepted`; a separate worker process performs the actual send. This keeps the API responsive
even when the mail server is slow or briefly down, and it lets the send be retried safely.

```
visitor → contact.html   (fetch POST /api/contact, JSON)
  → nginx                (reverse proxy: rate-limits /api/, proxies to the web app by name)
  → contact-api (web)    (Flask + gunicorn: validate, honeypot, ALTCHA; ENQUEUE the send → 202)
        │
        ▼  enqueue
  → redis                (the queue — holds pending sends; internal-only, password-protected)
        │
        ▼  dequeue
  → worker (Celery)      (builds the message, sends it, retries transient failures)
  → mail.keepbreath.ing:587   (STARTTLS + SASL as contact@, via host-gateway)
  → Postfix DKIM-signs + routes contact@ → Cloudflare → Gmail
```

Three services run together (see `docker-compose.yml`): **contact-api** (the web app),
**worker** (the Celery worker), and **redis** (the queue).

## How it sends mail

The worker authenticates as a normal **submission client** to `mail.keepbreath.ing:587`
(STARTTLS + SASL) using the `contact@` credential — the same path Gmail uses. Postfix
DKIM-signs the message and routes `contact@` → Cloudflare → Gmail. Because the worker
authenticates, Postfix's `mynetworks` stays loopback-only and no trust boundary moves.

Inside the container, `mail.keepbreath.ing` is mapped to the host via the compose
`extra_hosts: ["mail.keepbreath.ing:host-gateway"]`, so STARTTLS presents the real cert
(name matches → TLS verifies) with no public hairpin.

## Delivery: nothing is silently lost

Every message goes to one fixed address we control, so no message is ever permanently
undeliverable — a failure is always either a brief outage or our own misconfiguration, and both
clear. The worker is built around that fact, in four layers (`tasks.py`):

1. **Fast retries.** A send that hits a brief blip — a dropped connection, a timeout, a DNS
   failure, a TLS hiccup, or a 4xx "try later" SMTP reply — is retried up to 5 times with
   exponential backoff and jitter (1s, 2s, 4s… capped at 10 minutes), the pattern Google's SRE
   book recommends.
2. **The shelf.** Anything the fast layer doesn't deliver is parked on a Redis dead-letter list,
   tagged with the reason, the first-failure time, and a re-drive count.
3. **Automatic re-drive.** A scheduled sweep (Celery Beat, run inside the worker) re-sends
   everything on the shelf on an interval (hourly by default). A message that failed on a bad
   password sits and retries harmlessly until the password is fixed; a message from a long
   outage sits until mail returns. The shelf self-heals.
4. **Retention backstop.** The only message ever truly dropped is one that has sat on the shelf
   past a long age limit (3 days by default), and that drop is logged loudly as
   `dead_letter_expired`.

The transient-vs-permanent classification only chooses "fast-retry now" versus "straight to the
shelf" — never "give up." A message that can't even be built (malformed) is the one exception: it
is dropped immediately, since re-driving it would never succeed.

**Delivery is at-least-once.** If the worker sends a message and then crashes before it
acknowledges the task, the broker redelivers and the message may go out twice. For a contact form
a rare duplicate is the right trade against ever losing a message, but it is a real property, not
an accident.

## Configuration (environment)

Secrets come from a `.env` file (gitignored — copy `.env.example` and fill it in; never commit
it). Docker Compose reads `.env` for `${VAR}` substitution and hands **each service only the
variables it needs**, so the web app never receives the SMTP password:

| Variable | Used by | Purpose |
|---|---|---|
| `REDIS_PASSWORD`    | redis, web, worker | password for the Redis broker |
| `ALTCHA_HMAC_KEY`   | web                | signs the ALTCHA proof-of-work challenges |
| `CONTACT_SMTP_USER` | worker             | the `contact@` send-as login |
| `CONTACT_SMTP_PASS` | worker             | its submission password (from the lockbox) |

The broker URL and the replay-DB path are set per-service in `docker-compose.yml`, not in
`.env`.

## Run it

Built and run via Docker Compose. The web service joins the shared `keepbreath-net` network
that the site's nginx container is on, and publishes **no host ports** — only nginx reaches it,
over that network. Redis publishes no ports at all.

```
docker compose up -d --build
```

nginx proxies `/api/` to the web service at `http://contact-api:8000` over the shared network;
that proxy rule lives in the site repo's `nginx.conf` (single source of truth), not here.

## Worker sizing

- **Web (gunicorn):** the standard starting point is `(2 × CPU cores) + 1` workers. Because the
  send now runs in the Celery worker, these web workers no longer block on SMTP, so they stay
  fast. Tune under real load.
- **Worker (Celery):** `--concurrency` sets how many sends run at once. For this low volume, 2
  is plenty; raise it only if the queue backs up.

## Operations

- **Run exactly one scheduler.** The re-drive sweep runs via Celery Beat, embedded in the worker
  with `-B`. That's fine for a single worker, but if this ever scales to several workers, only
  **one** may run Beat — several `-B` workers would each fire the sweep and re-drive duplicates.
  At that point, move Beat to a dedicated `celery beat` service instead.
- **What to watch** in the logs (and, once the observability project lands, alert on):
  - `dead_letter_expired` — a message was finally dropped after 3 days on the shelf. Should be
    rare; if it fires, something was broken for days.
  - `redrive_runaway` — a single sweep re-drove an unusually large batch, a sign of a backlog.
  - `dead_letter_shelve_failed` — the one place a message can be lost (Redis rejected the write).
  - The Redis queue and dead-letter shelf depth.

## Tests

Unit tests cover the retry/dead-letter classification and the task's success and failure paths.
They run the task in-process (Celery "eager" mode), so no Redis or worker is needed:

```
pip install -r requirements-dev.txt
pytest
```

## Development

CI runs four gates: Ruff (lint + format), mypy (types), pytest, and a compose integration
smoke. Run the same checks locally before pushing so nothing is a surprise:

```
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
mypy .
pytest
```

The type checker is intentionally lenient (`ignore_missing_imports`) because Flask, Celery,
altcha and structlog ship no type stubs; that is configured in `pyproject.toml`.

## Security

- **Server-side validation** (never trust the client): types, length caps, and an RFC-aware
  email check via `email-validator`.
- **CRLF header-injection guard**: reject CR/LF in the name (it lands in the `Subject`), both in
  the web app's validation and again in the worker's `build_email` (defense in depth, since the
  worker trusts the queue); `EmailMessage` is a third backstop.
- **Three anti-bot layers**: honeypot, invisible self-hosted ALTCHA proof-of-work
  (`altcha==2.1.0`, pinned; ≥1.0.0 fixed CVE-2025-68113, with a single-use replay registry),
  and nginx rate limiting on `/api/`. The proof-of-work token is single-use and is consumed
  before the message is queued, so if queuing then fails the visitor solves a fresh challenge to
  retry — deliberate, to keep replay protection strict.
- **Least-privilege secrets**: the web app never holds the SMTP credentials — only the worker
  that actually sends does. The mail config lives in `tasks.py` and reads the credentials at
  send time.
- **Redis is internal-only**: no published ports, reachable only over the shared Docker
  network, password-protected, and memory-capped with a `noeviction` policy so a full queue
  rejects new writes rather than silently dropping a queued message.
- **PII in the queue** (open item): each queued message carries the visitor's name, email, and
  message, and it lives in Redis and its on-disk append-only file in plaintext until delivered.
  It's internal-only and password-protected, but it is a plaintext store while in flight;
  encrypting or minimising it at rest is tracked in issue #11.
- **Verifying TLS** on the outbound send (`ssl.create_default_context()`, TLS 1.2 floor).
- **Structured logging** (`structlog`), metadata only — never message content or secrets. A
  request id is carried from the web app onto the worker's log lines so an enqueue and its send
  can be tied together.
- **Hardened containers**: the web app and worker run non-root, read-only root filesystem,
  `no-new-privileges`, and all Linux capabilities dropped.
