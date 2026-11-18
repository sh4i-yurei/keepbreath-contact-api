import importlib.metadata
import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"
CHECK = ROOT / "scripts/check_lock.py"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scripts.check_lock", CHECK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Mutmut identifies this loaded file by its scripts.check_lock module name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pins(path: Path) -> dict[str, str]:
    return {
        name.lower(): version
        for line in path.read_text().splitlines()
        if (pin := line.split(" #", 1)[0].strip()) and "==" in pin
        for name, raw_version in [pin.split("==", 1)]
        for version in [raw_version.split()[0]]
    }


def test_installed_runtime_packages_match_the_lock(capsys: pytest.CaptureFixture[str]) -> None:
    assert LOCK.is_file()
    checker = load_checker()
    assert checker.main([str(LOCK)]) == 0
    assert capsys.readouterr().out == ""


def test_direct_dependencies_are_pinned_in_lock() -> None:
    assert LOCK.is_file()
    lock = pins(LOCK)
    direct = pins(ROOT / "requirements.txt")
    assert len(direct) == 9
    assert {name: lock.get(name) for name in direct} == direct


@pytest.mark.parametrize(
    ("installed", "exact", "expected_status", "expected_output"),
    [
        ({"Example_Pkg": "1.2"}, False, 0, ""),
        ({"example.pkg": "2.0"}, False, 1, "example-pkg: locked 1.2, installed 2.0\n"),
        ({}, False, 1, "example-pkg: locked 1.2, installed nothing\n"),
        (
            {"example-pkg": "1.2", "Extra_Pkg": "3.0"},
            True,
            1,
            "extra-pkg: installed 3.0, not in the lock\n",
        ),
        (
            {"example-pkg": "2.0", "Extra_Pkg": "3.0"},
            True,
            1,
            "example-pkg: locked 1.2, installed 2.0\nextra-pkg: installed 3.0, not in the lock\n",
        ),
    ],
)
def test_checker_reports_exact_package_differences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    installed: dict[str, str],
    exact: bool,
    expected_status: int,
    expected_output: str,
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("example_pkg==1.2 \\\n    --hash=sha256:abc\n")
    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: [
            SimpleNamespace(metadata={"Name": name}, version=version)
            for name, version in installed.items()
        ],
    )
    args = [str(lock)]
    if exact:
        args.insert(0, "--exact")
    assert load_checker().main(args) == expected_status
    assert capsys.readouterr().out == expected_output


def test_checker_help_describes_the_check(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        load_checker().main(["--help"])
    assert result.value.code == 0
    assert (
        "Compare installed Python packages with a pip-compile lock file." in capsys.readouterr().out
    )


def test_checker_script_guard_runs_from_stdin_style_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("example==1.0\n")
    monkeypatch.setattr(sys, "argv", [str(CHECK), str(lock)])
    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "example"}, version="1.0")],
    )
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(CHECK), run_name="__main__")
    assert result.value.code == 0
    assert capsys.readouterr().out == ""
