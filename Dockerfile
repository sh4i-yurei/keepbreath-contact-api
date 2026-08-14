# ============================================================
# keepbreath.ing — contact API (Dockerfile)
# Author: Mark Thompson
# ============================================================
# Builds the image that runs the Flask contact API under gunicorn.


# ------------------------------------------------------------
# BASE IMAGE
# A slim Python base keeps the image small and the attack surface low.
# Pinned to a digest for reproducible builds — the tag is documentation, the
# @sha256 is what actually gets pulled. Re-pin deliberately when updating.
# ------------------------------------------------------------

FROM python:3.13-slim@sha256:8fef26df932191825664e4957ff488c96dfe64918327634a357a55facbc994d3


# ------------------------------------------------------------
# PYTHON ENV
# PYTHONUNBUFFERED: don't buffer stdout — so our structlog JSON reaches
# `docker logs` immediately (buffered logs can be lost on a crash). This one
# matters given our whole logging strategy is "structured logs to stdout."
# PYTHONDONTWRITEBYTECODE: skip writing .pyc files into the image.
# ------------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1


# ------------------------------------------------------------
# DEPENDENCIES
# Copy requirements FIRST and install, so Docker caches this layer and
# doesn't reinstall every time the app code changes.
# ------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ------------------------------------------------------------
# APP SOURCE
# Copy only what the app needs (the .dockerignore keeps the rest out).
# ------------------------------------------------------------
COPY app.py replay.py ./


# ------------------------------------------------------------
# RUNTIME USER
# Run as a non-root user — least privilege if the app is ever compromised.
# ------------------------------------------------------------
RUN useradd --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

# ------------------------------------------------------------
# START
# gunicorn serves app:app on the port nginx proxies to over the shared network.
#   --worker-tmp-dir /dev/shm : keep gunicorn's heartbeat file on tmpfs (a
#     disk-backed /tmp can stall a worker) — also required for a read-only rootfs.
#   --access-logfile -        : access logs to stdout, alongside the structlog JSON.
# Tune workers/timeout for the blocking SMTP send (review note).
# ------------------------------------------------------------
EXPOSE 8000

# Liveness probe — a plain Python request (no need to install curl in the slim
# image). If /health can't be reached or returns non-2xx, urlopen raises and the
# check exits non-zero → Docker marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app", \
     "--workers", "2", "--timeout", "30", \
     "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-"]
