# keepbreath-contact-api

Contact-form backend for [keepbreath.ing](https://keepbreath.ing) — a small Flask
endpoint (`POST /api/contact`) that turns a form submission into an email, without
ever exposing a real inbox address.

> Scaffold — filled in as we build (each section becomes notes for the blog post
> `docs/blog/contact-form-backend.md` in the site repo).

## What it does

<!-- TODO: one paragraph — receives the form POST, validates, injects into local
     Postfix, which DKIM-signs and routes to contact@keepbreath.ing. -->

## How it sends mail (no password here)

<!-- TODO: explain local injection via localhost:25 + Postfix mynetworks trust;
     From = contact@, Reply-To = visitor. -->

## Local development

<!-- TODO: venv, pip install -r requirements.txt, run under gunicorn, curl test. -->

## Deploy

<!-- TODO: clone to droplet, venv, systemd unit (deploy/contact-api.service),
     nginx location + limit_req (deploy/nginx-contact-api.conf), reload. -->

## Security notes

<!-- TODO: server-side validation, honeypot, rate limiting, CRLF header-injection
     defense (EmailMessage + strip newlines). -->
