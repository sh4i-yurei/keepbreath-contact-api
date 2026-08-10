# ============================================================
# keepbreath.ing — contact API (Dockerfile)
# Author: Mark Thompson
# ============================================================
# Builds the image that runs the Flask contact API under gunicorn.
# Scaffold — directives filled in together (Docker, learn-by-doing).


# ------------------------------------------------------------
# BASE IMAGE
# A slim Python base keeps the image small and the attack surface low.
# ------------------------------------------------------------
# TODO: FROM python:3.13-slim  (match the version we develop against)


# ------------------------------------------------------------
# DEPENDENCIES
# Copy requirements FIRST and install, so Docker caches this layer and
# doesn't reinstall every time the app code changes.
# ------------------------------------------------------------
# TODO: WORKDIR /app
# TODO: COPY requirements.txt .
# TODO: RUN pip install --no-cache-dir -r requirements.txt


# ------------------------------------------------------------
# APP SOURCE
# Copy only what the app needs (the .dockerignore keeps the rest out).
# ------------------------------------------------------------
# TODO: COPY app.py replay.py ./


# ------------------------------------------------------------
# RUNTIME USER
# Run as a non-root user — least privilege if the app is ever compromised.
# ------------------------------------------------------------
# TODO: create a non-root user and USER it


# ------------------------------------------------------------
# START
# gunicorn serves app:app on the port nginx proxies to over the shared
# network. Tune workers/timeout for the blocking SMTP send (review note).
# ------------------------------------------------------------
# TODO: EXPOSE 8000
# TODO: CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app", "--workers", "?", "--timeout", "?"]
