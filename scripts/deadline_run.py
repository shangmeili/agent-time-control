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
    """Stop the isolated process group, including children of an exited leader."""

    if os.name == "posix":
        # Popen(start_new_session=True) makes the child's PID its process-group ID.
        # Never use leader exit as evidence that the whole group has stopped.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        grace_deadline = time.monotonic() + grace_seconds
        while True:
            process.poll()  # Reap the leader without losing the group identity.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                # An unsuccessful probe does not prove that the group is gone.
                # Still enforce the grace limit; actual signal errors propagate.
                pass
            remaining = grace_deadline - time.monotonic()
            if remaining <= 0:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # The last member exited between observation and signal.
                break
            time.sleep(min(0.01, remaining))
        process.wait(timeout=5)
        return

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class _TerminationRequested(BaseException):
    pass


def run_command(
    command: list[str], timeout_seconds: float, grace_seconds: float
) -> int:
    """Run without a shell; cleanup is also required on external SIGTERM."""
    process = None
    started = time.monotonic()
    previous_handler = None
    pending_signal = None

    def cleanup() -> bool:
        if process is None:
            return True
        try:
            stop_process(process, grace_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(
                json.dumps({"deadline_run": "cleanup_failed", "reason": str(exc)}),
                file=sys.stderr,
            )
            return False
        return True

    def terminate(signum, frame):
        nonlocal pending_signal
        pending_signal = signum
        if process is not None:
            raise _TerminationRequested(signum)

    if os.name == "posix":
        previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        try:
            process = subprocess.Popen(command, start_new_session=(os.name == "posix"))
            if pending_signal is not None:
                raise _TerminationRequested(pending_signal)
        except OSError as exc:
            print(
                json.dumps({"deadline_run": "launch_failed", "reason": str(exc)}),
                file=sys.stderr,
            )
            return 127 if isinstance(exc, FileNotFoundError) else 126
        try:
            returncode = process.wait(
                timeout=max(0.0, timeout_seconds - (time.monotonic() - started))
            )
        except subprocess.TimeoutExpired:
            if not cleanup():
                return 125
            print(
                json.dumps(
                    {
                        "deadline_run": "timed_out",
                        "elapsed_seconds": time.monotonic() - started,
                        "timeout_seconds": timeout_seconds,
                    }
                ),
                file=sys.stderr,
            )
            return TIMEOUT_EXIT_CODE
        if not cleanup():
            return 125
        return 128 - returncode if returncode < 0 else returncode
    except (KeyboardInterrupt, _TerminationRequested) as exc:
        if os.name == "posix":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if not cleanup():
            return 125
        return 130 if isinstance(exc, KeyboardInterrupt) else 128 + int(exc.args[0])
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps({"deadline_run": "cleanup_failed", "reason": str(exc)}),
            file=sys.stderr,
        )
        return 125
    finally:
        if os.name == "posix":
            signal.signal(signal.SIGTERM, previous_handler)


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

    return run_command(command, timeout_seconds, args.grace_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
