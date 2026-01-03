#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import NoReturn


def _die(msg: str, code: int = 2) -> NoReturn:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pytest with a hard wall-clock timeout.")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="Wall-clock timeout for the pytest run.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Arguments passed to pytest (prefix with --).")
    args = parser.parse_args()

    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    if not pytest_args:
        pytest_args = ["-q"]

    cmd = ["pytest", *pytest_args]
    try:
        proc = subprocess.run(cmd, timeout=int(args.timeout_seconds))
        raise SystemExit(int(proc.returncode))
    except subprocess.TimeoutExpired:
        _die(f"pytest run exceeded {args.timeout_seconds}s and was terminated.", code=124)


if __name__ == "__main__":
    main()




