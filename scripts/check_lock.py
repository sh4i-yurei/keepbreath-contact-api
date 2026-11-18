"""Compare installed Python packages with a pip-compile lock file."""

import argparse
import importlib.metadata
import re
from pathlib import Path


PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("lock", type=Path)
    args = parser.parse_args(argv)

    locked: dict[str, str] = {}
    for line in args.lock.read_text().splitlines():
        if match := PIN.match(line):
            locked[normalized(match.group(1))] = match.group(2)

    installed = {
        normalized(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    mismatches = 0
    for name, version in sorted(locked.items()):
        actual = installed.get(name)
        if actual != version:
            print(f"{name}: locked {version}, installed {actual or 'nothing'}")
            mismatches += 1
    if args.exact:
        for name in sorted(installed.keys() - locked.keys()):
            print(f"{name}: installed {installed[name]}, not in the lock")
            mismatches += 1
    return int(mismatches != 0)


if __name__ == "__main__":
    raise SystemExit(main())
