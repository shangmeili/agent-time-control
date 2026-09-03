from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from budget_gate import decide
from deadline_clock import build_snapshot, parse_timestamp


def run_script(
    name: str, *args: str, stdin: str | None = None, timeout: float = 5
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class DeadlineClockTests(unittest.TestCase):
    def test_requires_timezone(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_timestamp("2026-09-02T16:00:00")

    def test_execute_reserve_and_expired_phases(self) -> None:
        deadline = parse_timestamp("2026-09-02T16:00:00+08:00")
        execute = build_snapshot(
            deadline=deadline,
            now=parse_timestamp("2026-09-02T15:00:00+08:00"),
            reserve_seconds=1800,
        )
        reserve = build_snapshot(
            deadline=deadline,
            now=parse_timestamp("2026-09-02T15:45:00+08:00"),
            reserve_seconds=1800,
        )
        expired = build_snapshot(
            deadline=deadline,
            now=parse_timestamp("2026-09-02T16:00:01+08:00"),
            reserve_seconds=1800,
        )
        self.assertEqual(execute["phase"], "execute")
        self.assertEqual(reserve["phase"], "reserve")
        self.assertEqual(expired["phase"], "expired")

    def test_relative_timebox_keeps_original_start(self) -> None:
        result = run_script(
            "deadline_clock.py",
            "--now",
            "2026-09-02T14:30:00+08:00",
            "--started-at",
            "2026-09-02T14:00:00+08:00",
            "--duration-minutes",
            "45",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["deadline"], "2026-09-02T14:45:00+08:00")
        self.assertEqual(payload["elapsed_seconds"], 1800)
        self.assertEqual(payload["remaining_seconds"], 900)


class BudgetGateTests(unittest.TestCase):
    @staticmethod
    def snapshot(now: str, reserve_seconds: float = 600) -> dict[str, object]:
        return build_snapshot(
            deadline=parse_timestamp("2026-09-02T15:00:00+08:00"),
            now=parse_timestamp(now),
            reserve_seconds=reserve_seconds,
        )

    def test_all_execution_decisions(self) -> None:
        snapshot = self.snapshot("2026-09-02T14:00:00+08:00")
        cases = [
            ((600, 1200, 1800), "continue"),
            ((600, 1200, 3600), "continue_core_only"),
            ((600, 3600, 4200), "replan_and_reduce_scope"),
            ((3600, 4000, 5000), "reduce_scope_or_handoff"),
        ]
        for estimates, expected_action in cases:
            with self.subTest(action=expected_action):
                result = decide(
                    snapshot,
                    low_seconds=estimates[0],
                    likely_seconds=estimates[1],
                    high_seconds=estimates[2],
                    multiplier=1.0,
                )
                self.assertEqual(result["action"], expected_action)

    def test_calibration_multiplier_can_change_decision(self) -> None:
        snapshot = self.snapshot("2026-09-02T14:00:00+08:00")
        unadjusted = decide(
            snapshot,
            low_seconds=1200,
            likely_seconds=2000,
            high_seconds=2400,
            multiplier=1.0,
        )
        adjusted = decide(
            snapshot,
            low_seconds=1200,
            likely_seconds=2000,
            high_seconds=2400,
            multiplier=2.0,
        )
        self.assertEqual(unadjusted["action"], "continue")
        self.assertEqual(adjusted["action"], "replan_and_reduce_scope")

    def test_reserve_and_expired_are_non_overridable(self) -> None:
        reserve = decide(
            self.snapshot("2026-09-02T14:55:00+08:00"),
            low_seconds=0,
            likely_seconds=0,
            high_seconds=0,
            multiplier=1.0,
        )
        expired = decide(
            self.snapshot("2026-09-02T15:00:01+08:00"),
            low_seconds=0,
            likely_seconds=0,
            high_seconds=0,
            multiplier=1.0,
        )
        self.assertEqual(reserve["action"], "verify_and_handoff")
        self.assertEqual(expired["action"], "stop")


class CalibrationReportTests(unittest.TestCase):
    def test_report_includes_failures_and_completed_error(self) -> None:
        records = [
            {
                "task_class": "bugfix",
                "actual_elapsed_seconds": 100,
                "estimated_likely_seconds": 100,
                "outcome": "complete",
            },
            {
                "task_class": "bugfix",
                "actual_elapsed_seconds": 200,
                "estimated_likely_seconds": 100,
                "outcome": "complete",
            },
            {
                "task_class": "bugfix",
                "actual_elapsed_seconds": 500,
                "estimated_likely_seconds": 200,
                "outcome": "failed",
            },
        ]
        source = "".join(json.dumps(record) + "\n" for record in records)
        result = run_script("calibration_report.py", "--input", "-", stdin=source)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["records"], 3)
        self.assertEqual(payload["outcomes"], {"complete": 2, "failed": 1})
        self.assertAlmostEqual(payload["completion_rate"], 2 / 3)
        self.assertEqual(payload["completed_actual_over_likely_estimate"]["p50"], 1.5)

    def test_invalid_record_is_rejected(self) -> None:
        result = run_script(
            "calibration_report.py",
            "--input",
            "-",
            stdin='{"actual_elapsed_seconds": "fast"}\n',
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be numeric", result.stderr)


class DeadlineRunTests(unittest.TestCase):
    def test_fast_command_preserves_exit_code_and_output(self) -> None:
        result = run_script(
            "deadline_run.py",
            "--timeout-seconds",
            "2",
            "--",
            sys.executable,
            "-c",
            'print("ok")',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_slow_command_is_terminated(self) -> None:
        result = run_script(
            "deadline_run.py",
            "--timeout-seconds",
            "0.1",
            "--grace-seconds",
            "0.1",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        )
        self.assertEqual(result.returncode, 124)
        self.assertIn('"deadline_run": "timed_out"', result.stderr)

    def test_past_deadline_does_not_launch_command(self) -> None:
        result = run_script(
            "deadline_run.py",
            "--deadline",
            "2000-01-01T00:00:00Z",
            "--",
            sys.executable,
            "-c",
            'print("must-not-run")',
        )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertIn('"deadline_run": "not_started"', result.stderr)


if __name__ == "__main__":
    unittest.main()
