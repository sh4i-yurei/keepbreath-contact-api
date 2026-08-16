# Test that the worker entry point aborts on invalid config, so a misconfigured worker
# never starts. worker.py validates WorkerSettings() at import time, so importing it with
# the mail credentials missing must fail. Run in a subprocess with a stripped environment,
# which is the honest way to test import-time behaviour and locks it into CI rather than
# relying on a manual container check.
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_worker_import_aborts_without_smtp_credentials():
    env = {
        k: v for k, v in os.environ.items() if k not in ("CONTACT_SMTP_USER", "CONTACT_SMTP_PASS")
    }
    env.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")

    proc = subprocess.run(
        [sys.executable, "-c", "import worker"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0  # the import must abort, not succeed
    assert "ValidationError" in proc.stderr
    assert "contact_smtp" in proc.stderr.lower()  # the error names the missing credential
