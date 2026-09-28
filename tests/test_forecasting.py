from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control.controller import TimeBudgetController, TimeContract
from agent_time_control.forecasting import forecast_remaining_work


def observations(operation="verify", values=(1, 2, 3, 4, 5), **overrides):
    return [
        {
            "operation": operation,
            "reference_class": "same-host-model-tools",
            "elapsed_seconds": value,
            "outcome": "complete",
            **overrides,
        }
        for value in values
    ]


class ForecastingTests(unittest.TestCase):
    def test_cli_returns_estimate_and_rejects_malformed_requests(self):
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "forecast_remaining.py"
        )
        request = {
            "remaining_steps": ["verify"],
            "observations": observations(),
            "reference_class": "same-host-model-tools",
        }
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--input", "-"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["remaining_work_interval"]["likely_seconds"], 3
        )
        for invalid in ("[]", "{}", "not json"):
            with self.subTest(invalid=invalid):
                result = subprocess.run(
                    [sys.executable, "-B", str(script), "--input", "-"],
                    input=invalid,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def forecast(self, steps, records):
        return forecast_remaining_work(
            steps, records, reference_class="same-host-model-tools"
        )

    def test_serial_remaining_steps_include_measured_model_latency(self):
        history = observations() + observations("model", (2, 3, 4, 5, 6))
        result = self.forecast(["model", "verify"], history)
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(
            result["remaining_work_interval"],
            {"low_seconds": 3, "likely_seconds": 7, "high_seconds": 11},
        )
        self.assertEqual(result["sample_counts"], {"model": 5, "verify": 5})
        self.assertEqual(result["uncertainty_status"], "empirical_range_not_calibrated")

    def test_repeated_operations_are_counted_and_finished_work_is_removed(self):
        history = observations()
        result = self.forecast(["verify", "verify"], history)
        self.assertEqual(result["remaining_work_interval"]["likely_seconds"], 6)
        self.assertEqual(
            self.forecast(["verify"], history)["remaining_work_interval"][
                "likely_seconds"
            ],
            3,
        )
        self.assertEqual(
            self.forecast([], history)["remaining_work_interval"]["high_seconds"], 0
        )

    def test_unknown_operation_does_not_get_zero_duration(self):
        result = self.forecast(["verify", "unknown"], observations())
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["missing_operations"], ["unknown"])
        self.assertIsNone(result["remaining_work_interval"])

    def test_one_observation_cannot_masquerade_as_a_range(self):
        result = self.forecast(["verify"], observations(values=(1,)))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["remaining_work_interval"])

    def test_unrelated_reference_class_is_not_pooled(self):
        result = self.forecast(["verify"], observations(reference_class="other-model"))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["sample_counts"]["verify"], 0)

    def test_failed_or_censored_observations_prevent_success_only_forecast(self):
        for outcome in ("failed", "timed_out", "partial"):
            with self.subTest(outcome=outcome):
                records = observations() + observations(values=(8,), outcome=outcome)
                result = self.forecast(["verify"], records)
                self.assertIsNone(result["remaining_work_interval"])
                self.assertEqual(result["censored_counts"], {"verify": 1})

    def test_bad_observation_is_rejected(self):
        for value in (True, "1", -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                self.forecast(["verify"], observations(values=(value,)))
        with self.assertRaises(ValueError):
            self.forecast(["verify"], observations(outcome="unknown"))

    def test_input_history_is_not_mutated(self):
        records = observations()
        original = copy.deepcopy(records)
        self.forecast(["verify"], records)
        self.assertEqual(records, original)

    def test_controller_exposes_source_and_does_not_clamp_to_budget(self):
        controller = TimeBudgetController(TimeContract.relative(2))
        result = controller.update_forecast_from_history(
            ["verify"], observations(), reference_class="same-host-model-tools"
        )
        self.assertEqual(result["remaining_work_interval"]["high_seconds"], 5)
        self.assertNotEqual(result["action"], "continue")
        self.assertEqual(
            result["forecast_evidence"]["source"], "observed_step_durations"
        )
        self.assertIn("observed_step_durations", controller.model_context())

    def test_missing_history_invalidates_previous_forecast(self):
        controller = TimeBudgetController(TimeContract.relative(100))
        controller.update_forecast(1, 2, 3)
        result = controller.update_forecast_from_history(
            ["verify"], [], reference_class="same-host-model-tools"
        )
        self.assertEqual(result["forecast_status"], "missing")
        self.assertNotIn("remaining_work_interval", controller.checkpoint())

    def test_manual_update_drops_stale_history_metadata(self):
        controller = TimeBudgetController(TimeContract.relative(100))
        controller.update_forecast_from_history(
            ["verify"], observations(), reference_class="same-host-model-tools"
        )
        controller.update_forecast(1, 2, 3)
        self.assertNotIn("forecast_evidence", controller.checkpoint())
