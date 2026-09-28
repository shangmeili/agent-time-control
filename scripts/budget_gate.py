#!/usr/bin/env python3
"""Turn a wall-clock snapshot and remaining-work interval into a control action."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.core import (
    build_snapshot,
    create_timebox,
    decide,
)
from agent_time_control.core import parse_timestamp as _parse_timestamp


def parse_timestamp(value: str) -> datetime:
    try:
        return _parse_timestamp(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def finite_nonnegative(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate whether estimated remaining work fits a real time budget."
    )
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument("--deadline", type=parse_timestamp)
    limit.add_argument("--duration-minutes", type=finite_nonnegative)
    parser.add_argument("--started-at", type=parse_timestamp)
    parser.add_argument("--reserve-minutes", type=finite_nonnegative, default=0.0)
    parser.add_argument(
        "--estimate-low-seconds", type=finite_nonnegative, required=True
    )
    parser.add_argument(
        "--estimate-likely-seconds", type=finite_nonnegative, required=True
    )
    parser.add_argument(
        "--estimate-high-seconds", type=finite_nonnegative, required=True
    )
    parser.add_argument(
        "--calibration-multiplier",
        type=finite_nonnegative,
        default=1.0,
        help="Observed actual/estimate multiplier from a comparable reference class.",
    )
    parser.add_argument("--now", type=parse_timestamp, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.duration_minutes is not None and args.duration_minutes <= 0:
        parser.error("--duration-minutes must be positive")
    if args.calibration_multiplier <= 0:
        parser.error("--calibration-multiplier must be positive")
    if not (
        args.estimate_low_seconds
        <= args.estimate_likely_seconds
        <= args.estimate_high_seconds
    ):
        parser.error("remaining-work estimates must satisfy low <= likely <= high")

    now = args.now or datetime.now(timezone.utc)
    started_at = args.started_at
    try:
        deadline = args.deadline
        if args.duration_minutes is not None:
            started_at = started_at or now
            box = create_timebox(
                duration_seconds=args.duration_minutes * 60,
                now=started_at,
                reserve_seconds=args.reserve_minutes * 60,
            )
            deadline = _parse_timestamp(box["deadline"])
        snapshot = build_snapshot(
            deadline=deadline,
            now=now,
            started_at=started_at,
            reserve_seconds=args.reserve_minutes * 60,
        )
        result = decide(
            snapshot,
            low_seconds=args.estimate_low_seconds,
            likely_seconds=args.estimate_likely_seconds,
            high_seconds=args.estimate_high_seconds,
            multiplier=args.calibration_multiplier,
        )
    except (ValueError, OverflowError) as exc:
        parser.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
