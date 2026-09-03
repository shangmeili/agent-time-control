from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_time_control.evaluation import (
    evaluate_conditions,
    evaluate_pilot_advancement,
)


def record(run_id: str, condition: str, **overrides):
    value = {
        "run_id": run_id,
        "task_id": "task-1",
        "condition": condition,
        "model": "model-v1",
        "tool_profile": "tools-v1",
        "budget_seconds": 100,
        "actual_elapsed_seconds": 90,
        "outcome": "complete",
        "deadline_met": True,
        "verified_utility": 1.0,
        "first_infeasible_warning_elapsed_seconds": None,
        "checkpoints": [
            {
                "estimate_low_seconds": 30,
                "estimate_likely_seconds": 50,
                "estimate_high_seconds": 70,
                "actual_remaining_seconds": 60,
            }
        ],
    }
    value.update(overrides)
    return value


def complete_pilot_records() -> list[dict]:
    records = []
    for task_index in range(3):
        for repetition in range(3):
            seed = 100 + task_index + repetition * 3
            for condition in ("base", "rules", "tracked", "controller"):
                records.append(
                    record(
                        f"{task_index}-{repetition}-{condition}",
                        condition,
                        task_id=f"task-{task_index}",
                        repetition=repetition,
                        sample_seed=seed,
                        error=None,
                    )
                )
    return records


class EvaluationTests(unittest.TestCase):
    def test_reports_matched_condition_metrics_and_warnings(self) -> None:
        result = evaluate_conditions(
            [
                record(
                    "base-1",
                    "base",
                    outcome="timed_out",
                    deadline_met=False,
                    verified_utility=0.4,
                    actual_elapsed_seconds=100,
                ),
                record(
                    "controlled-1",
                    "controlled",
                    outcome="failed",
                    deadline_met=True,
                    verified_utility=0.6,
                    first_infeasible_warning_elapsed_seconds=40,
                ),
            ]
        )
        self.assertTrue(result["matched_design"])
        self.assertEqual(result["conditions"]["base"]["deadline_violation_rate"], 1.0)
        self.assertEqual(
            result["conditions"]["controlled"]["early_warning_lead_seconds"]["p50"],
            60.0,
        )
        self.assertEqual(
            result["conditions"]["controlled"]["checkpoint_interval_coverage"],
            1.0,
        )

    def test_flags_unmatched_tasks_and_design_mismatch(self) -> None:
        records = [
            record("a", "base"),
            record("b", "controlled", budget_seconds=200),
            record("c", "base", task_id="task-only-in-base"),
        ]
        result = evaluate_conditions(records)
        self.assertFalse(result["matched_design"])
        self.assertEqual(result["unmatched_tasks"], ["task-only-in-base"])
        self.assertEqual(result["task_budget_or_tool_mismatches"], ["task-1"])

    def test_flags_unequal_repetition_counts(self) -> None:
        result = evaluate_conditions(
            [
                record("base-1", "base"),
                record("base-2", "base"),
                record("controlled-1", "controlled"),
            ]
        )
        self.assertFalse(result["matched_design"])
        self.assertEqual(result["task_condition_count_mismatches"], ["task-1"])

    def test_flags_incomplete_pairing_and_seed_mismatch(self) -> None:
        incomplete = evaluate_conditions(
            [
                record("base-0", "base", repetition=0, sample_seed=10),
                record("controlled-1", "controlled", repetition=1, sample_seed=11),
            ]
        )
        self.assertFalse(incomplete["matched_design"])
        self.assertEqual(
            incomplete["paired_block_mismatches"],
            ["task-1:0", "task-1:1"],
        )

        seed_mismatch = evaluate_conditions(
            [
                record("base-0", "base", repetition=0, sample_seed=10),
                record("controlled-0", "controlled", repetition=0, sample_seed=11),
            ]
        )
        self.assertFalse(seed_mismatch["matched_design"])
        self.assertEqual(seed_mismatch["paired_seed_mismatches"], ["task-1:0"])

    def test_duplicate_run_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate run_id"):
            evaluate_conditions([record("same", "base"), record("same", "control")])

    def test_pilot_advancement_gate_passes_complete_paired_records(self) -> None:
        records = complete_pilot_records()
        result = evaluate_pilot_advancement(records, expected_records=36)
        self.assertTrue(result["passed"])

    def test_pilot_advancement_gate_reports_late_controller_return(self) -> None:
        records = complete_pilot_records()
        late = next(record for record in records if record["condition"] == "controller")
        late["actual_elapsed_seconds"] = 101
        result = evaluate_pilot_advancement(records, expected_records=36)
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["diagnostics"]["controller_late_return_run_ids"],
            [late["run_id"]],
        )

    def test_pilot_gate_rejects_a_short_two_condition_smoke(self) -> None:
        records = [
            record("base-0", "base", repetition=0, sample_seed=10),
            record("controller-0", "controller", repetition=0, sample_seed=10),
        ]
        with self.assertRaisesRegex(ValueError, "rules, tracked"):
            evaluate_pilot_advancement(records, expected_records=2)
