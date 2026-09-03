#!/usr/bin/env python3
"""Evaluate JSONL records from matched time-awareness conditions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.calibration import read_records
from agent_time_control.evaluation import evaluate_conditions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute descriptive metrics for matched time-control evaluations."
    )
    parser.add_argument("--input", required=True, help="JSONL file, or - for stdin")
    args = parser.parse_args()
    try:
        if args.input == "-":
            records = read_records(sys.stdin)
        else:
            with Path(args.input).open(encoding="utf-8") as handle:
                records = read_records(handle)
        result = evaluate_conditions(records)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
