"""Forecast a serial remaining-work plan from caller-authorized timing records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.forecasting import forecast_remaining_work


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, help="JSON request file, or - for stdin"
    )
    args = parser.parse_args()
    try:
        if args.input == "-":
            request = json.load(sys.stdin)
        else:
            with Path(args.input).open(encoding="utf-8") as handle:
                request = json.load(handle)
        if not isinstance(request, dict):
            raise TypeError("request must be an object")
        result = forecast_remaining_work(
            request["remaining_steps"],
            request["observations"],
            reference_class=request["reference_class"],
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
