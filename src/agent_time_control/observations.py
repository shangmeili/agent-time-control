"""Opt-in, bounded in-memory timing observations; never records payloads or writes files."""

from __future__ import annotations

import asyncio
import math
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .forecasting import MAX_OBSERVATIONS


class TimingRecorder:
    """Measure authorized operations, including failed and cancelled invocations.

    Use ``with recorder.measure("operation"):`` around synchronous or awaited work.
    Export ``snapshot()`` only to a destination authorized by the host. A full
    recorder rejects entry before new work; it never evicts failed measurements.
    Thread/task-safe collection does not imply that overlapping work is serial.
    """

    def __init__(
        self,
        reference_class: str,
        *,
        max_records: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(reference_class, str) or not reference_class.strip():
            raise ValueError(
                "reference_class must identify comparable model, tools and host"
            )
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 1 <= max_records <= MAX_OBSERVATIONS
        ):
            raise ValueError(
                f"max_records must be an integer between 1 and {MAX_OBSERVATIONS}"
            )
        self.reference_class = reference_class
        self._max_records = max_records
        self._clock = clock
        self._records: list[dict[str, object]] = []
        self._active = 0
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, operation: str) -> Iterator[None]:
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation must be a non-empty name")
        started = self._clock()
        if not math.isfinite(started):
            raise ValueError("measurement clock must be finite")
        with self._lock:
            if len(self._records) + self._active >= self._max_records:
                raise BufferError(
                    "timing recorder is full; export and start a new recorder"
                )
            self._active += 1
        outcome = "complete"
        try:
            yield
        except TimeoutError:
            outcome = "timed_out"
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            outcome = "partial"
            raise
        except BaseException:
            outcome = "failed"
            raise
        finally:
            try:
                elapsed = self._clock() - started
                valid = math.isfinite(elapsed) and elapsed >= 0
            except Exception:  # noqa: BLE001 - timing failure must not mask the operation's error.
                elapsed, valid = 0.0, False
            # Invalid timing evidence is never passed off as a successful zero duration.
            record = {
                "observation_id": str(uuid.uuid4()),
                "reference_class": self.reference_class,
                "operation": operation,
                "elapsed_seconds": elapsed if valid else 0.0,
                "outcome": outcome if valid else "failed",
            }
            if not valid:
                record["measurement_error"] = "invalid_monotonic_clock"
            with self._lock:
                self._active -= 1
                self._records.append(record)

    def snapshot(self) -> list[dict[str, object]]:
        """Return copies of completed measurements; in-flight operations are absent."""
        with self._lock:
            return [dict(record) for record in self._records]
