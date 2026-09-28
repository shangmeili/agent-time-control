from __future__ import annotations

import asyncio
import unittest

from agent_time_control.forecasting import forecast_remaining_work
from agent_time_control.observations import TimingRecorder


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    def test_measures_real_operations_and_forecasts_without_manual_records(self):
        clock = [0.0]
        recorder = TimingRecorder("same-workload", clock=lambda: clock[0])
        for duration in (1, 2, 3, 4, 5):
            with recorder.measure("verify"):
                clock[0] += duration
        records = recorder.snapshot()
        self.assertEqual(len({r["observation_id"] for r in records}), 5)
        self.assertEqual([r["elapsed_seconds"] for r in records], [1, 2, 3, 4, 5])
        result = forecast_remaining_work(
            ["verify"], records, reference_class="same-workload"
        )
        self.assertEqual(result["remaining_work_interval"]["likely_seconds"], 3)
        self.assertNotIn("arguments", records[0])
        self.assertNotIn("result", records[0])
        records[0]["elapsed_seconds"] = 999
        self.assertEqual(recorder.snapshot()[0]["elapsed_seconds"], 1)

    async def test_failures_timeouts_and_cancellation_are_retained(self):
        recorder = TimingRecorder("test")
        for exc, expected in (
            (ValueError("failure"), "failed"),
            (TimeoutError(), "timed_out"),
            (asyncio.CancelledError(), "partial"),
        ):
            with self.assertRaises(type(exc)), recorder.measure("op"):
                raise exc
            self.assertEqual(recorder.snapshot()[-1]["outcome"], expected)
            self.assertNotIn("failure", str(recorder.snapshot()[-1]))

    async def test_overlapping_async_measurements_have_distinct_records(self):
        recorder = TimingRecorder("test")

        async def measure():
            with recorder.measure("same-name"):
                await asyncio.sleep(0)

        await asyncio.gather(*(measure() for _ in range(10)))
        self.assertEqual(len(recorder.snapshot()), 10)
        self.assertEqual(len({r["observation_id"] for r in recorder.snapshot()}), 10)

    def test_capacity_fails_before_work_and_never_silently_discards_failures(self):
        recorder = TimingRecorder("test", max_records=1)
        with self.assertRaises(ValueError), recorder.measure("op"):
            raise ValueError("failed")
        called = False
        with self.assertRaises(BufferError), recorder.measure("op"):
            called = True
        self.assertFalse(called)
        self.assertEqual(recorder.snapshot()[0]["outcome"], "failed")

    def test_invalid_metadata_and_capacity_rejected(self):
        for capacity in (True, 0, 10001):
            with self.assertRaises(ValueError):
                TimingRecorder("test", max_records=capacity)
        with self.assertRaises(ValueError):
            TimingRecorder("")
        with self.assertRaises(ValueError), TimingRecorder("test").measure(""):
            pass
