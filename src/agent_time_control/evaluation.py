"""Condition-level metrics for matched time-awareness experiments."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .calibration import numeric_summary

OUTCOMES = {"complete", "partial", "failed", "timed_out"}


def _number(
    record: dict[str, Any], field: str, index: int, *, maximum: float | None = None
) -> float:
    value = record.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"record {index}: {field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"record {index}: {field} must be finite and non-negative")
    if maximum is not None and number > maximum:
        raise ValueError(f"record {index}: {field} must be <= {maximum}")
    return number


def _validate_record(record: dict[str, Any], index: int) -> None:
    for field in ("run_id", "task_id", "condition", "model", "tool_profile"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError(f"record {index}: {field} must be a non-empty string")
    _number(record, "budget_seconds", index)
    _number(record, "actual_elapsed_seconds", index)
    _number(record, "verified_utility", index, maximum=1.0)
    if record.get("outcome") not in OUTCOMES:
        raise ValueError(f"record {index}: outcome must be one of {sorted(OUTCOMES)}")
    if not isinstance(record.get("deadline_met"), bool):
        raise TypeError(f"record {index}: deadline_met must be boolean")
    for field in ("repetition", "sample_seed"):
        if field in record and (
            not isinstance(record[field], int)
            or isinstance(record[field], bool)
            or record[field] < 0
        ):
            raise TypeError(f"record {index}: {field} must be a non-negative integer")
    warning = record.get("first_infeasible_warning_elapsed_seconds")
    if warning is not None:
        _number(record, "first_infeasible_warning_elapsed_seconds", index)
    checkpoints = record.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        raise TypeError(f"record {index}: checkpoints must be an array")
    for checkpoint_index, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            raise TypeError(
                f"record {index} checkpoint {checkpoint_index}: expected an object"
            )
        for field in (
            "estimate_low_seconds",
            "estimate_likely_seconds",
            "estimate_high_seconds",
            "actual_remaining_seconds",
        ):
            _number(checkpoint, field, index)
        if not (
            checkpoint["estimate_low_seconds"]
            <= checkpoint["estimate_likely_seconds"]
            <= checkpoint["estimate_high_seconds"]
        ):
            raise ValueError(
                f"record {index} checkpoint {checkpoint_index}: estimates must satisfy low <= likely <= high"
            )


def evaluate_conditions(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate matched conditions without claiming statistical significance."""

    if not records:
        raise ValueError("at least one evaluation record is required")
    seen_run_ids: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_designs: dict[str, set[tuple[str, str, float]]] = defaultdict(set)
    task_condition_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    paired_blocks: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    repetition_presence: list[bool] = []
    sample_seed_presence: list[bool] = []

    for index, record in enumerate(records, start=1):
        _validate_record(record, index)
        if record["run_id"] in seen_run_ids:
            raise ValueError(f"record {index}: duplicate run_id {record['run_id']!r}")
        seen_run_ids.add(record["run_id"])
        repetition_presence.append("repetition" in record)
        sample_seed_presence.append("sample_seed" in record)
        grouped[record["condition"]].append(record)
        task_condition_counts[record["task_id"]][record["condition"]] += 1
        task_designs[record["task_id"]].add(
            (
                record["model"],
                record["tool_profile"],
                float(record["budget_seconds"]),
            )
        )
        if "repetition" in record:
            paired_blocks[(record["task_id"], record["repetition"])].append(record)

    if any(repetition_presence) and not all(repetition_presence):
        raise ValueError("repetition must be present in every record or none")
    if any(sample_seed_presence) and not all(sample_seed_presence):
        raise ValueError("sample_seed must be present in every record or none")
    if any(sample_seed_presence) and not any(repetition_presence):
        raise ValueError("sample_seed requires repetition identifiers")

    condition_names = sorted(grouped)
    tasks_by_condition = {
        condition: {record["task_id"] for record in condition_records}
        for condition, condition_records in grouped.items()
    }
    all_tasks = set().union(*tasks_by_condition.values())
    unmatched_tasks = sorted(
        task
        for task in all_tasks
        if any(task not in tasks for tasks in tasks_by_condition.values())
    )
    design_mismatches = sorted(
        task for task, designs in task_designs.items() if len(designs) > 1
    )
    count_mismatches = sorted(
        task
        for task, counts in task_condition_counts.items()
        if len(counts) != len(condition_names) or len(set(counts.values())) != 1
    )
    paired_block_mismatches: list[str] = []
    paired_seed_mismatches: list[str] = []
    if paired_blocks:
        expected_conditions = set(condition_names)
        for (task, repetition), block_records in paired_blocks.items():
            block_id = f"{task}:{repetition}"
            if {
                record["condition"] for record in block_records
            } != expected_conditions or len(block_records) != len(expected_conditions):
                paired_block_mismatches.append(block_id)
            if (
                all("sample_seed" in record for record in block_records)
                and len({record["sample_seed"] for record in block_records}) != 1
            ):
                paired_seed_mismatches.append(block_id)
        paired_block_mismatches.sort()
        paired_seed_mismatches.sort()

    condition_results: dict[str, Any] = {}
    for condition in condition_names:
        condition_records = grouped[condition]
        elapsed: list[float] = []
        utility: list[float] = []
        checkpoint_errors: list[float] = []
        checkpoint_widths: list[float] = []
        checkpoint_hits = 0
        checkpoint_count = 0
        warning_leads: list[float] = []

        for record in condition_records:
            budget = float(record["budget_seconds"])
            elapsed.append(float(record["actual_elapsed_seconds"]))
            utility.append(float(record["verified_utility"]))
            warning = record.get("first_infeasible_warning_elapsed_seconds")
            if record["outcome"] in {"failed", "timed_out"} and warning is not None:
                warning_leads.append(max(0.0, budget - float(warning)))
            for checkpoint in record.get("checkpoints", []):
                actual = float(checkpoint["actual_remaining_seconds"])
                low = float(checkpoint["estimate_low_seconds"])
                likely = float(checkpoint["estimate_likely_seconds"])
                high = float(checkpoint["estimate_high_seconds"])
                checkpoint_count += 1
                checkpoint_hits += int(low <= actual <= high)
                checkpoint_errors.append(abs(actual - likely))
                checkpoint_widths.append(high - low)

        complete = sum(record["outcome"] == "complete" for record in condition_records)
        violations = sum(not record["deadline_met"] for record in condition_records)
        failed_runs = sum(
            record["outcome"] in {"failed", "timed_out"} for record in condition_records
        )
        condition_results[condition] = {
            "runs": len(condition_records),
            "models": sorted({record["model"] for record in condition_records}),
            "completion_rate": complete / len(condition_records),
            "deadline_violation_rate": violations / len(condition_records),
            "verified_utility_mean": sum(utility) / len(utility),
            "actual_elapsed_seconds": numeric_summary(elapsed),
            "checkpoint_count": checkpoint_count,
            "checkpoint_interval_coverage": (
                checkpoint_hits / checkpoint_count if checkpoint_count else None
            ),
            "checkpoint_absolute_likely_error_seconds": numeric_summary(
                checkpoint_errors
            ),
            "checkpoint_interval_width_seconds": numeric_summary(checkpoint_widths),
            "failed_or_timed_out_runs": failed_runs,
            "early_warning_lead_seconds": numeric_summary(warning_leads),
            "failed_runs_without_warning": failed_runs - len(warning_leads),
        }

    return {
        "schema_version": "1.0",
        "records": len(records),
        "conditions": condition_results,
        "matched_design": not unmatched_tasks
        and not design_mismatches
        and not count_mismatches
        and not paired_block_mismatches
        and not paired_seed_mismatches,
        "paired_design": (
            not paired_block_mismatches and not paired_seed_mismatches
            if paired_blocks
            else None
        ),
        "unmatched_tasks": unmatched_tasks,
        "task_budget_or_tool_mismatches": design_mismatches,
        "task_condition_count_mismatches": count_mismatches,
        "paired_block_mismatches": paired_block_mismatches,
        "paired_seed_mismatches": paired_seed_mismatches,
        "interpretation_note": (
            "descriptive metrics only; use repeated randomized matched runs before causal or significance claims"
        ),
    }


def evaluate_pilot_advancement(
    records: list[dict[str, Any]],
    *,
    expected_records: int,
    utility_regression_tolerance: float = 0.1,
    completion_block_tolerance: int = 1,
    deadline_return_tolerance_seconds: float = 0.5,
    minimum_records: int = 36,
    minimum_task_templates: int = 3,
    minimum_matched_blocks: int = 9,
) -> dict[str, Any]:
    """Apply the preregistered synthetic-pilot advancement gate."""

    if expected_records <= 0:
        raise ValueError("expected_records must be positive")
    if not 0 <= utility_regression_tolerance <= 1:
        raise ValueError("utility_regression_tolerance must be between zero and one")
    if completion_block_tolerance < 0:
        raise ValueError("completion_block_tolerance must be non-negative")
    if deadline_return_tolerance_seconds < 0:
        raise ValueError("deadline_return_tolerance_seconds must be non-negative")
    if min(minimum_records, minimum_task_templates, minimum_matched_blocks) <= 0:
        raise ValueError("pilot minimums must be positive")

    evaluation = evaluate_conditions(records)
    required_conditions = {"base", "rules", "tracked", "controller"}
    available_conditions = set(evaluation["conditions"])
    missing_conditions = sorted(required_conditions - available_conditions)
    if missing_conditions:
        raise ValueError(
            f"pilot gate requires conditions: {', '.join(missing_conditions)}"
        )

    base_records = [record for record in records if record["condition"] == "base"]
    controller_records = [
        record for record in records if record["condition"] == "controller"
    ]
    base_complete = sum(record["outcome"] == "complete" for record in base_records)
    controller_complete = sum(
        record["outcome"] == "complete" for record in controller_records
    )
    base_utility = evaluation["conditions"]["base"]["verified_utility_mean"]
    controller_utility = evaluation["conditions"]["controller"]["verified_utility_mean"]
    controller_late_returns = [
        record["run_id"]
        for record in controller_records
        if float(record["actual_elapsed_seconds"])
        > float(record["budget_seconds"]) + deadline_return_tolerance_seconds
    ]
    controller_errors = [
        record["run_id"] for record in controller_records if record.get("error")
    ]

    checks = {
        "all_planned_records_retained": len(records) == expected_records,
        "minimum_records_reached": len(records) >= minimum_records,
        "minimum_task_templates_reached": (
            len({record["task_id"] for record in records}) >= minimum_task_templates
        ),
        "minimum_matched_blocks_reached": (
            len(
                {
                    (record["task_id"], record["repetition"])
                    for record in records
                    if "repetition" in record
                }
            )
            >= minimum_matched_blocks
        ),
        "matched_design": evaluation["matched_design"] is True,
        "paired_design": evaluation["paired_design"] is True,
        "controller_returned_within_deadline_tolerance": not controller_late_returns,
        "no_controller_harness_errors": not controller_errors,
        "utility_regression_within_tolerance": (
            controller_utility >= base_utility - utility_regression_tolerance
        ),
        "completion_regression_within_tolerance": (
            controller_complete >= base_complete - completion_block_tolerance
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "expected_records": expected_records,
            "utility_regression_tolerance": utility_regression_tolerance,
            "completion_block_tolerance": completion_block_tolerance,
            "deadline_return_tolerance_seconds": deadline_return_tolerance_seconds,
            "minimum_records": minimum_records,
            "minimum_task_templates": minimum_task_templates,
            "minimum_matched_blocks": minimum_matched_blocks,
        },
        "diagnostics": {
            "controller_late_return_run_ids": controller_late_returns,
            "controller_error_run_ids": controller_errors,
            "base_complete": base_complete,
            "controller_complete": controller_complete,
            "base_utility_mean": base_utility,
            "controller_utility_mean": controller_utility,
        },
        "evaluation": evaluation,
    }
