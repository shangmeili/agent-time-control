#!/usr/bin/env python3
"""Run one already-authorized subprocess with a host-enforced wall-clock limit."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.core import parse_timestamp as _parse_timestamp


def parse_timestamp(value: str) -> datetime:
    try:
        return _parse_timestamp(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


TIMEOUT_EXIT_CODE = 124


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def stop_process(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGKILL)
    else:
        process.kill()
    process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a command without allowing it to cross a wall-clock limit."
    )
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument("--deadline", type=parse_timestamp)
    limit.add_argument("--timeout-seconds", type=positive_float)
    parser.add_argument(
        "--reserve-seconds",
        type=float,
        default=0.0,
        help="Keep this much time unused before an absolute deadline.",
    )
    parser.add_argument("--grace-seconds", type=positive_float, default=2.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if not math.isfinite(args.reserve_seconds) or args.reserve_seconds < 0:
        parser.error("--reserve-seconds must be finite and non-negative")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    now = datetime.now(timezone.utc)
    if args.deadline is not None:
        timeout_seconds = (
            args.deadline.astimezone(timezone.utc) - now
        ).total_seconds() - args.reserve_seconds
    else:
        timeout_seconds = args.timeout_seconds
        assert timeout_seconds is not None

    if timeout_seconds <= 0:
        print(
            json.dumps(
                {
                    "deadline_run": "not_started",
                    "reason": "no execution budget remains",
                    "timeout_seconds": timeout_seconds,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return TIMEOUT_EXIT_CODE

    started = time.monotonic()
    process = subprocess.Popen(
        command,
        start_new_session=(os.name == "posix"),
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_process(process, args.grace_seconds)
        elapsed = time.monotonic() - started
        print(
            json.dumps(
                {
                    "deadline_run": "timed_out",
                    "elapsed_seconds": elapsed,
                    "timeout_seconds": timeout_seconds,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return TIMEOUT_EXIT_CODE
    except KeyboardInterrupt:
        stop_process(process, args.grace_seconds)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
