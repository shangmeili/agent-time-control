from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from evaluate_forecasts import summarize


class ForecastEvaluationTests(unittest.TestCase):
    def record(self, outcome="complete", missing=False):
        return {
            "outcome": outcome,
            "actual_remaining_seconds": 2,
            "predictions": {
                name: {
                    "interval": None if missing else [1, 2, 3],
                    "forecast_cost_seconds": 0.1,
                }
                for name in ("original_prompt", "grounded_prompt", "empirical_steps")
            },
        }

    def test_failed_targets_and_invalid_forecasts_are_retained(self):
        result = summarize(
            [self.record(), self.record(outcome="timed_out"), self.record(missing=True)]
        )
        for metrics in result.values():
            self.assertEqual(metrics["records"], 3)
            self.assertEqual(metrics["scored_completed"], 1)
            self.assertEqual(metrics["invalid_forecasts"], 1)
            self.assertEqual(metrics["interval_coverage"], 1)
            self.assertEqual(metrics["median_interval_width_seconds"], 2)

    def test_no_usable_prediction_never_gets_perfect_accuracy(self):
        result = summarize([self.record(missing=True)])
        for metrics in result.values():
            self.assertIsNone(metrics["median_absolute_error_seconds"])
            self.assertIsNone(metrics["interval_coverage"])
