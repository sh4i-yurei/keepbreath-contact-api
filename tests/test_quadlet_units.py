"""Run Quadlet from Podman's 5.8.2 image, the version web ran when written.

Run the API health check through upstream 5.7.0 too, the version Ubuntu 26.04
ships, whose Quadlet dropped the old health command's closing quote.

The default test run leaves these tests out.
"""

import io
import json
import os
import shlex
import subprocess
import tarfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.quadlet

IMAGE = (
    "quay.io/podman/stable@sha256:663e0dbf407987b7db3f20d3588c283a8228db17b282d2029a482d4d47e36964"
)
IMAGE_5_7_0 = (
    "quay.io/podman/stable@sha256:c852d2bb3081494f4ab933062ab6f31875de4ea5c5e1003d2e205c60bd3bdd25"
)
UNITS = Path(__file__).resolve().parents[1] / "deploy/quadlet"
DROP_IN = "contact-worker.container.d/50-relay.conf"

WEB_RELAY = """[Container]
Environment=CONTACT_SMTP_USER=contact@keepbreath.ing
Environment=CONTACT_SMTP_HOST=mail.keepbreath.ing
Environment=CONTACT_SMTP_PORT=2587
AddHost=mail.keepbreath.ing:10.128.0.4
"""
STAGING_RELAY = """[Container]
Environment=CONTACT_SMTP_USER=staging
Environment=CONTACT_SMTP_HOST=mailpit.staging
Environment=CONTACT_SMTP_PORT=1025
AddHost=mailpit.staging:10.89.200.10
"""


def _unit_archive(drop_in: str | None) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(UNITS.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(UNITS))
        if drop_in is not None:
            contents = drop_in.encode()
            info = tarfile.TarInfo(DROP_IN)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
    return stream.getvalue()


def _quadlet_output(image: str, drop_in: str | None) -> str:
    engine = shlex.split(os.environ.get("ENGINE", "podman"))
    command = [
        *engine,
        "run",
        "-i",
        "--rm",
        image,
        "sh",
        "-c",
        "mkdir -p /u && tar -C /u -xf - && QUADLET_UNIT_DIRS=/u /usr/libexec/podman/quadlet -dryrun -user",
    ]
    environment = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
        if name in os.environ
    }
    result = subprocess.run(
        command, input=_unit_archive(drop_in), capture_output=True, env=environment
    )

    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode()


@pytest.mark.parametrize(
    ("drop_in", "expected"),
    [
        pytest.param(None, [], id="no drop-in"),
        pytest.param(
            WEB_RELAY,
            [
                "add-host mail.keepbreath.ing:10.128.0.4",
                "env CONTACT_SMTP_HOST=mail.keepbreath.ing",
                "env CONTACT_SMTP_PORT=2587",
                "env CONTACT_SMTP_USER=contact@keepbreath.ing",
            ],
            id="web's drop-in",
        ),
        pytest.param(
            STAGING_RELAY,
            [
                "add-host mailpit.staging:10.89.200.10",
                "env CONTACT_SMTP_HOST=mailpit.staging",
                "env CONTACT_SMTP_PORT=1025",
                "env CONTACT_SMTP_USER=staging",
            ],
            id="staging's drop-in",
        ),
    ],
)
def test_the_worker_relay_comes_only_from_the_drop_in(
    drop_in: str | None, expected: list[str]
) -> None:
    output = _quadlet_output(IMAGE, drop_in)
    marker = "---contact-worker.service---"
    assert marker in output, "Quadlet generated no contact-worker.service"
    worker = output.split(marker, 1)[1].split("---", 1)[0]
    exec_start = next(
        line.removeprefix("ExecStart=")
        for line in worker.splitlines()
        if line.startswith("ExecStart=")
    )
    arguments = shlex.split(exec_start)
    actual = []
    for index, argument in enumerate(arguments[:-1]):
        if argument == "--add-host":
            actual.append(f"add-host {arguments[index + 1]}")
        if argument == "--env" and arguments[index + 1].startswith("CONTACT_SMTP_"):
            actual.append(f"env {arguments[index + 1]}")
    assert sorted(actual) == sorted(expected)


@pytest.mark.parametrize(
    "image",
    [
        pytest.param(IMAGE, id="podman 5.8.2"),
        pytest.param(IMAGE_5_7_0, id="podman 5.7.0"),
    ],
)
def test_the_api_health_check_survives_quadlet(image: str) -> None:
    output = _quadlet_output(image, None)
    marker = "---contact-api.service---"
    assert marker in output, "Quadlet generated no contact-api.service"
    api = output.split(marker, 1)[1].split("---", 1)[0]
    exec_start = next(
        line.removeprefix("ExecStart=")
        for line in api.splitlines()
        if line.startswith("ExecStart=")
    )
    arguments = shlex.split(exec_start)
    health_command = arguments[arguments.index("--health-cmd") + 1].replace("\\x20", " ")
    assert health_command.startswith("["), (
        f"the API's health check is not in the exec form: {health_command}"
    )
    assert json.loads(health_command) == [
        "python",
        "-c",
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')",
    ]
