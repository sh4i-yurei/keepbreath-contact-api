# ============================================================
# keepbreath.ing — contact API (replay.py)
# Author: Mark Thompson
# ============================================================
# SQLite single-use registry for ALTCHA challenge signatures. A given challenge
# can be accepted exactly once; a repeat is a replay and gets rejected. Safe
# across multiple gunicorn workers because the database's UNIQUE constraint
# enforces single-use — no application-level locking needed.

import os
import sqlite3
import time

# DB file path — override with REPLAY_DB_PATH. On the droplet this points at a
# mounted volume so the used-signature history survives container restarts.
DB_PATH = os.environ.get("REPLAY_DB_PATH", "altcha_replay.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
    conn.execute("PRAGMA busy_timeout=5000")  # wait out brief locks instead of erroring
    return conn


def init_db():
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS used_challenges ("
                "signature TEXT PRIMARY KEY, "
                "expires_at INTEGER NOT NULL)"
            )
    finally:
        conn.close()


def try_reserve(signature, expires_at):
    """Record a challenge signature as used.

    Returns True if it was fresh (first time seen) or False if it was already
    used (a replay). The UNIQUE PRIMARY KEY makes this atomic even when several
    workers hit it at once — the second INSERT raises IntegrityError.
    """
    now = int(time.time())
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM used_challenges WHERE expires_at < ?", (now,))
            try:
                conn.execute(
                    "INSERT INTO used_challenges (signature, expires_at) VALUES (?, ?)",
                    (signature, expires_at),
                )
            except sqlite3.IntegrityError:
                return False
        return True
    finally:
        conn.close()
