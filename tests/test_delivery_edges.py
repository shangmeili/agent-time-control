from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deadline_run import run_command

from agent_time_control import TimeBudgetController, TimeContract, TimingRecorder


class DeliveryEdgeTests(unittest.TestCase):
    def test_monotonic_clock_exhausts_budget_when_wall_clock_is_frozen(self):
        wall = datetime.now(timezone.utc)
        elapsed = [100.0]
        controller = TimeBudgetController(
            TimeContract.relative(5, now=wall),
            clock=lambda: wall,
            monotonic_clock=lambda: elapsed[0],
        )
        elapsed[0] = 106.0
        self.assertEqual(controller.snapshot()["phase"], "expired")

    def test_subsecond_verification_can_use_remaining_fractional_budget(self):
        wall = datetime.now(timezone.utc)
        controller = TimeBudgetController(
            TimeContract.relative(0.8, reserve_seconds=0.7, now=wall),
            clock=lambda: wall + timedelta(seconds=0.3),
        )
        self.assertEqual(
            controller.require_action_allowed(
                estimated_seconds=0.1, action_kind="verification"
            )["phase"],
            "reserve",
        )

    def test_recorder_clock_failure_never_masks_work_error_or_leaks_capacity(self):
        clock = Mock(side_effect=[1.0, RuntimeError("clock failed"), 3.0, 4.0])
        recorder = TimingRecorder("test", max_records=2, clock=clock)
        with (
            self.assertRaisesRegex(ValueError, "work failed"),
            recorder.measure("work"),
        ):
            raise ValueError("work failed")
        with recorder.measure("work"):
            pass
        records = recorder.snapshot()
        self.assertEqual(records[0]["measurement_error"], "invalid_monotonic_clock")
        self.assertEqual(records[0]["outcome"], "failed")
        self.assertEqual(records[1]["outcome"], "complete")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_sigterm_during_launch_is_deferred_until_child_is_owned(self):
        process = Mock()

        def launch(*args, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return process

        with (
            patch("deadline_run.subprocess.Popen", side_effect=launch),
            patch("deadline_run.stop_process") as cleanup,
        ):
            result = run_command(["synthetic"], 10, 0.1)
        self.assertEqual(result, 143)
        cleanup.assert_called_once_with(process, 0.1)

    def test_cleanup_timeout_is_not_reported_as_successful_work_timeout(self):
        process = Mock()
        process.wait.return_value = 0
        error = io.StringIO()
        with (
            patch("deadline_run.subprocess.Popen", return_value=process),
            patch(
                "deadline_run.stop_process",
                side_effect=subprocess.TimeoutExpired("cleanup", 5),
            ),
            redirect_stderr(error),
        ):
            result = run_command(["synthetic"], 10, 0.1)
        self.assertEqual(result, 125)
        self.assertIn("cleanup_failed", error.getvalue())
        self.assertNotIn('"timed_out"', error.getvalue())
