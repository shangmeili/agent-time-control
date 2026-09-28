from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_time_control.controller import TimeBudgetController, TimeContract
from agent_time_control.core import build_snapshot, create_timebox, decide
from agent_time_control.forecasting import forecast_remaining_work


class DeliveryInputTests(unittest.TestCase):
    def test_boolean_and_text_are_not_durations(self):
        for duration in (True, "30"):
            with (
                self.subTest(duration=duration),
                self.assertRaises((ValueError, TypeError)),
            ):
                create_timebox(
                    duration_seconds=duration, now=datetime.now(timezone.utc)
                )

    def test_estimate_product_overflow_is_rejected(self):
        state = build_snapshot(
            deadline=datetime.now(timezone.utc) + timedelta(seconds=60),
            now=datetime.now(timezone.utc),
        )
        with self.assertRaises(ValueError):
            decide(
                state,
                low_seconds=1e308,
                likely_seconds=1e308,
                high_seconds=1e308,
                multiplier=2,
            )

    def test_out_of_range_timebox_has_validation_error(self):
        with self.assertRaises(ValueError):
            create_timebox(duration_seconds=1e300, now=datetime.now(timezone.utc))

    def test_invalid_start_deadline_contract_is_rejected(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            build_snapshot(deadline=now, now=now, started_at=now + timedelta(seconds=1))

    def test_duplicate_observation_ids_cannot_inflate_sample_count(self):
        records = [
            {
                "observation_id": "same",
                "reference_class": "test",
                "operation": "verify",
                "elapsed_seconds": 1.0,
                "outcome": "complete",
            }
        ] * 5
        with self.assertRaises(ValueError):
            forecast_remaining_work(["verify"], records, reference_class="test")

    def test_cli_rejects_reserve_larger_than_relative_budget(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts/deadline_clock.py"),
                "--duration-minutes",
                "1",
                "--reserve-minutes",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_command_returns_conventional_exit_code(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts/deadline_run.py"),
                "--timeout-seconds",
                "1",
                "--",
                str(ROOT / "does-not-exist-command"),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(json.loads(result.stderr)["deadline_run"], "launch_failed")


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_clock_closes_unstarted_coroutine(self):
        controller = TimeBudgetController(TimeContract.relative(60))
        controller._clock = lambda: datetime(2026, 1, 1)  # noqa: DTZ001 - intentional invalid-clock input.

        async def work():
            return 1

        coroutine = work()
        try:
            with self.assertRaises(ValueError):
                await controller.run_until_hard_deadline(coroutine)
            self.assertIsNone(
                coroutine.cr_frame, "invalid clock leaked an unawaited coroutine"
            )
        finally:
            coroutine.close()

    async def test_clock_rollback_never_reopens_expired_window(self):
        start = datetime.now(timezone.utc)
        clock = [start]
        controller = TimeBudgetController(
            TimeContract.relative(10, now=start), clock=lambda: clock[0]
        )
        clock[0] = start + timedelta(seconds=11)
        self.assertEqual(controller.snapshot()["phase"], "expired")
        clock[0] = start
        self.assertEqual(controller.snapshot()["phase"], "expired")


@unittest.skipUnless(os.name == "posix", "requires POSIX signals and process groups")
class DeliveryProcessTests(unittest.TestCase):
    def test_wrapper_sigterm_cleans_up_child(self):
        child = "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True); time.sleep(30)"
        wrapper = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts/deadline_run.py"),
                "--timeout-seconds",
                "30",
                "--grace-seconds",
                "0.05",
                "--",
                sys.executable,
                "-c",
                child,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child_pid = None
        try:
            assert wrapper.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(wrapper.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), "child did not start")
                child_pid = int(wrapper.stdout.readline())
                wrapper.terminate()
                self.assertEqual(wrapper.wait(timeout=3), 143)
                self.assertTrue(
                    selector.select(2),
                    "child still holds pipe after wrapper termination",
                )
                self.assertEqual(wrapper.stdout.read(), b"")
        finally:
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if wrapper.poll() is None:
                wrapper.kill()
            wrapper.wait(timeout=5)
            if wrapper.stdout is not None:
                wrapper.stdout.close()
            if wrapper.stderr is not None:
                wrapper.stderr.close()
