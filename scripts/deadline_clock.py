#!/usr/bin/env python3
"""Return a deterministic wall-clock snapshot for deadline-aware agent work."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.core import build_snapshot
from agent_time_control.core import parse_timestamp as _parse_timestamp


def parse_timestamp(value: str) -> datetime:
    try:
        return _parse_timestamp(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect time remaining until a deadline or within a relative timebox."
    )
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument("--deadline", type=parse_timestamp)
    limit.add_argument(
        "--duration-minutes",
        type=float,
        help="Relative timebox measured from --started-at, or from now on first use.",
    )
    parser.add_argument("--started-at", type=parse_timestamp)
    parser.add_argument(
        "--reserve-minutes",
        type=float,
        default=0.0,
        help="Minutes reserved before the hard deadline for verification or handoff.",
    )
    parser.add_argument(
        "--now",
        type=parse_timestamp,
        help="Override the current time for reproducible tests only.",
    )
    args = parser.parse_args()

    if not math.isfinite(args.reserve_minutes) or args.reserve_minutes < 0:
        parser.error("--reserve-minutes must be a finite non-negative number")
    if args.duration_minutes is not None and (
        not math.isfinite(args.duration_minutes) or args.duration_minutes <= 0
    ):
        parser.error("--duration-minutes must be a finite positive number")

    now = args.now or datetime.now(timezone.utc)
    started_at = args.started_at
    if args.duration_minutes is not None:
        started_at = started_at or now
        deadline = started_at + timedelta(minutes=args.duration_minutes)
        payload = build_snapshot(
            deadline=deadline,
            now=now,
            started_at=started_at,
            reserve_seconds=args.reserve_minutes * 60,
        )
    else:
        deadline = args.deadline
        assert deadline is not None
        payload = build_snapshot(
            deadline=deadline,
            now=now,
            started_at=started_at,
            reserve_seconds=args.reserve_minutes * 60,
        )

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
