"""Read-only empirical summaries for comparable agent task histories."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 0.50),
        "p80": percentile(values, 0.80),
        "p90": percentile(values, 0.90),
        "max": max(values) if values else None,
    }


def read_records(lines: Iterable[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise TypeError(f"line {line_number}: expected a JSON object")
        records.append(record)
    return records


def _number(record: dict[str, object], field: str, index: int) -> float:
    value = record.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"record {index}: {field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"record {index}: {field} must be finite and non-negative")
    return number


def summarize_records(
    records: list[dict[str, object]], task_class: str | None = None
) -> dict[str, object]:
    """Summarize outcomes, duration error, and interval coverage without writing data."""

    selected = (
        [record for record in records if record.get("task_class") == task_class]
        if task_class is not None
        else list(records)
    )
    if not selected:
        raise ValueError("no matching records")

    all_elapsed: list[float] = []
    completed_elapsed: list[float] = []
    completed_ratios: list[float] = []
    all_observed_ratios: list[float] = []
    interval_widths: list[float] = []
    interval_observations = 0
    interval_hits = 0
    outcomes: dict[str, int] = {}

    for index, record in enumerate(selected, start=1):
        actual = _number(record, "actual_elapsed_seconds", index)
        outcome = record.get("outcome", "unknown")
        if not isinstance(outcome, str) or not outcome:
            raise ValueError(f"record {index}: outcome must be a non-empty string")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        all_elapsed.append(actual)

        estimate_value = record.get("estimated_likely_seconds")
        if estimate_value is not None:
            estimate = _number(record, "estimated_likely_seconds", index)
            if estimate > 0:
                all_observed_ratios.append(actual / estimate)
                if outcome == "complete":
                    completed_ratios.append(actual / estimate)

        low_value = record.get("estimated_low_seconds")
        high_value = record.get("estimated_high_seconds")
        if outcome == "complete" and low_value is not None and high_value is not None:
            low = _number(record, "estimated_low_seconds", index)
            high = _number(record, "estimated_high_seconds", index)
            if high < low:
                raise ValueError(
                    f"record {index}: estimated_low_seconds must not exceed estimated_high_seconds"
                )
            interval_observations += 1
            interval_hits += int(low <= actual <= high)
            interval_widths.append(high - low)

        if outcome == "complete":
            completed_elapsed.append(actual)

    completed_count = outcomes.get("complete", 0)
    completed_ratio_summary = numeric_summary(completed_ratios)
    sample_note = (
        "insufficient sample for a calibration claim"
        if len(selected) < 10
        else "check task, model, tools, and environment comparability before reuse"
    )
    if completed_count < len(selected):
        sample_note += "; completed-run ratios may have survivorship bias"

    return {
        "task_class": task_class,
        "records": len(selected),
        "outcomes": outcomes,
        "completion_rate": completed_count / len(selected),
        "all_observed_elapsed_seconds": numeric_summary(all_elapsed),
        "completed_elapsed_seconds": numeric_summary(completed_elapsed),
        "all_observed_actual_over_likely_estimate": numeric_summary(
            all_observed_ratios
        ),
        "completed_actual_over_likely_estimate": completed_ratio_summary,
        "completed_interval_coverage": (
            interval_hits / interval_observations if interval_observations else None
        ),
        "completed_interval_observations": interval_observations,
        "completed_interval_width_seconds": numeric_summary(interval_widths),
        "empirical_p80_multiplier": completed_ratio_summary["p80"],
        "confidence_note": sample_note,
    }
