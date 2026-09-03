#!/usr/bin/env python3
"""Summarize observed task durations and forecast error from JSONL history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.calibration import (
    read_records,
    summarize_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only calibration report from task-history JSONL."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="JSONL file, or - to read standard input.",
    )
    parser.add_argument("--task-class", help="Exact task_class value to include.")
    args = parser.parse_args()

    try:
        if args.input == "-":
            records = read_records(sys.stdin)
        else:
            with Path(args.input).open(encoding="utf-8") as handle:
                records = read_records(handle)

        result = summarize_records(records, args.task_class)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
