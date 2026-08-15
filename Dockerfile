# ============================================================
# keepbreath.ing — contact API (Dockerfile)
# Author: Mark Thompson
# ============================================================
# Builds the image that runs the Flask contact API under gunicorn.


# ------------------------------------------------------------
# BASE IMAGE
# Alpine, not Debian slim. A Python API needs Python and its libraries and
# nothing else — but Debian's base ships Perl, gzip, tar, ncurses, and the
# login/passwd tooling, none of which this app ever touches and all of which
# carry their own security vulnerabilities. Alpine ships almost none of it,
# which took the image from 182 known vulnerabilities to zero. Pinned to a
# digest for reproducible builds; re-pin deliberately when updating.
# ------------------------------------------------------------
FROM python:3.13-alpine@sha256:42825e7ec3437b3bce923c237484eb23d32128476e18307d2f48951bf86f1db2


# ------------------------------------------------------------
# PYTHON ENV
# PYTHONUNBUFFERED keeps stdout unbuffered, so the structlog JSON reaches
# `docker logs` immediately (buffered logs can be lost on a crash).
# PYTHONDONTWRITEBYTECODE skips writing .pyc files into the image.
# HOME=/tmp exists because gunicorn's control socket wants to live under the
# home directory, and the home directory sits on the read-only root filesystem.
# Pointing HOME at the writable in-memory /tmp gives it a legitimate place to
# write instead of erroring on every boot.
# ------------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp


# ------------------------------------------------------------
# DEPENDENCIES
# Copy requirements first and install, so Docker caches this layer and doesn't
# reinstall every time the app code changes. Then delete pip, setuptools, and
# wheel: those are only needed to *install* packages, never to *run* the app, so
# leaving them in the final image would just carry their vulnerabilities for no
# benefit. (This is what clears the last few findings the base image can't.)
# ------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip* \
              /usr/local/lib/python3.13/site-packages/setuptools* \
              /usr/local/lib/python3.13/site-packages/pkg_resources \
              /usr/local/lib/python3.13/site-packages/_distutils_hack \
              /usr/local/lib/python3.13/site-packages/wheel* \
              /usr/local/bin/pip* /usr/local/bin/wheel*


# ------------------------------------------------------------
# APP SOURCE
# Copy only what the app needs (the .dockerignore keeps the rest out).
# ------------------------------------------------------------
COPY app.py replay.py ./


# ------------------------------------------------------------
# RUNTIME USER
# Run as a non-root user, so a compromise of the app doesn't start as root.
# Alpine's adduser comes from BusyBox, so its flags differ from Debian's
# useradd: -D creates the user with no password, and -h sets the home directory.
# /data is chowned to appuser here so the app can write its replay database once
# the named volume mounts over that path at runtime.
# ------------------------------------------------------------
RUN adduser -D -h /home/appuser appuser \
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

# Liveness probe — a plain Python request (no need to install curl in the minimal
# image). If /health can't be reached or returns non-2xx, urlopen raises and the
# check exits non-zero, so Docker marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app", \
     "--workers", "2", "--timeout", "30", \
     "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-"]
