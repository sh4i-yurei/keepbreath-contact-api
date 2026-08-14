# keepbreath-contact-api

Contact-form backend for [keepbreath.ing](https://keepbreath.ing) — a small Flask API
(`POST /api/contact`) that turns a form submission into an email so a visitor can reach me
and I can reply as `contact@keepbreath.ing`, without ever exposing a real inbox address.

## What it does

Receives the JSON form POST, validates it server-side (required fields, length caps, an
RFC-aware email check, and a CRLF header-injection guard), runs three anti-bot layers
(honeypot, invisible ALTCHA proof-of-work, nginx rate limiting), builds the message with
`email.message.EmailMessage`, and sends it. `From:` is `contact@keepbreath.ing` and
`Reply-To:` is the visitor — hitting reply goes to them; my Gmail is never shown.

## How it sends mail

The app authenticates as a normal **submission client** to `mail.keepbreath.ing:587`
(STARTTLS + SASL) using the `contact@` credential — the same path Gmail uses. Postfix
DKIM-signs the message and routes `contact@` → Cloudflare → Gmail. Because the app
authenticates, Postfix's `mynetworks` stays loopback-only and no trust boundary moves.

Inside the container, `mail.keepbreath.ing` is mapped to the host via the compose
`extra_hosts: ["mail.keepbreath.ing:host-gateway"]`, so STARTTLS presents the real cert
(name matches → TLS verifies) with no public hairpin.

```
visitor → contact.html  (fetch POST /api/contact, JSON)
  → nginx           (reverse proxy: rate-limits /api/, proxies to the container by name)
  → contact-api     (this app — Flask + gunicorn: validate, honeypot, ALTCHA, build mail)
  → mail.keepbreath.ing:587   (STARTTLS + SASL as contact@, via host-gateway)
  → Postfix DKIM-signs + routes contact@ → Cloudflare → Gmail
```

## Configuration (environment)

Runtime config comes from `contact-api.env` (gitignored — copy `contact-api.env.example`
and fill it in on the droplet; never commit it):

| Variable | Purpose |
|---|---|
| `CONTACT_SMTP_USER` | the `contact@` send-as login |
| `CONTACT_SMTP_PASS` | its submission password (from the lockbox) |
| `ALTCHA_HMAC_KEY`   | signs the ALTCHA proof-of-work challenges |
| `REPLAY_DB_PATH`    | path to the SQLite single-use registry (a mounted volume) |

## Run it

Built and run via Docker Compose. The service joins the shared `keepbreath-net` network
that the site's nginx container is on, and publishes **no host ports** — only nginx reaches
it, over that network:

```
docker compose up -d --build
```

nginx proxies `/api/` to this service at `http://contact-api:8000` over the shared network;
that proxy rule lives in the site repo's `nginx.conf` (single source of truth), not here.

## Security

- **Server-side validation** (never trust the client): types, length caps, and an RFC-aware
  email check via `email-validator`.
- **CRLF header-injection guard**: reject CR/LF in the name (it lands in the `Subject`);
  `EmailMessage` is the backstop.
- **Three anti-bot layers**: honeypot, invisible self-hosted ALTCHA proof-of-work
  (`altcha>=1.0.0`, pinned for CVE-2025-68113, with a single-use replay registry), and
  nginx rate limiting on `/api/`.
- **Verifying TLS** on the outbound send (`ssl.create_default_context()`, TLS 1.2 floor).
- **Structured logging** (`structlog`), metadata only — never message content or secrets.
- **Hardened container**: runs non-root, read-only root filesystem, `no-new-privileges`,
  and all Linux capabilities dropped.
