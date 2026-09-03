"""Host-side controller for automatic checkpoints and hard run limits."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from .core import build_snapshot, decide

T = TypeVar("T")


class HardDeadlineReached(TimeoutError):
    """Raised when the host deadline has been reached."""


class NewWorkWindowClosed(RuntimeError):
    """Raised when only the verification or handoff reserve remains."""


@dataclass(frozen=True)
class TimeContract:
    """One immutable wall-clock contract for an agent run."""

    started_at: datetime
    deadline: datetime
    reserve_seconds: float = 0.0
    clock_source: str = "host_system_clock"

    def __post_init__(self) -> None:
        for field, value in (
            ("started_at", self.started_at),
            ("deadline", self.deadline),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} must include timezone information")
        if self.deadline <= self.started_at:
            raise ValueError("deadline must be later than started_at")
        total = (self.deadline - self.started_at).total_seconds()
        if (
            not math.isfinite(self.reserve_seconds)
            or self.reserve_seconds < 0
            or self.reserve_seconds > total
        ):
            raise ValueError(
                "reserve_seconds must be between zero and the total budget"
            )

    @classmethod
    def relative(
        cls,
        duration_seconds: float,
        *,
        reserve_seconds: float = 0.0,
        now: datetime | None = None,
    ) -> TimeContract:
        observed = now or datetime.now(timezone.utc)
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        return cls(
            started_at=observed,
            deadline=observed + timedelta(seconds=duration_seconds),
            reserve_seconds=reserve_seconds,
        )


class TimeBudgetController:
    """Refresh time state automatically and turn forecasts into control actions."""

    def __init__(
        self,
        contract: TimeContract,
        *,
        clock: Callable[[], datetime] | None = None,
        calibration_multiplier: float = 1.0,
    ) -> None:
        if not math.isfinite(calibration_multiplier) or calibration_multiplier <= 0:
            raise ValueError("calibration_multiplier must be positive")
        self.contract = contract
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.calibration_multiplier = calibration_multiplier
        self._forecast: tuple[float, float, float] | None = None

    def update_forecast(
        self, low_seconds: float, likely_seconds: float, high_seconds: float
    ) -> dict[str, object]:
        """Store the newest progressive remaining-work estimate and evaluate it."""

        snapshot = self.snapshot()
        state = decide(
            snapshot,
            low_seconds=low_seconds,
            likely_seconds=likely_seconds,
            high_seconds=high_seconds,
            multiplier=self.calibration_multiplier,
        )
        self._forecast = (low_seconds, likely_seconds, high_seconds)
        return state

    def snapshot(self) -> dict[str, object]:
        observed = self._clock()
        return build_snapshot(
            deadline=self.contract.deadline,
            now=observed,
            started_at=self.contract.started_at,
            reserve_seconds=self.contract.reserve_seconds,
            clock_source=self.contract.clock_source,
        )

    def checkpoint(self) -> dict[str, object]:
        snapshot = self.snapshot()
        if self._forecast is None:
            return {**snapshot, "forecast_status": "missing"}
        return decide(
            snapshot,
            low_seconds=self._forecast[0],
            likely_seconds=self._forecast[1],
            high_seconds=self._forecast[2],
            multiplier=self.calibration_multiplier,
        )

    def require_new_work_allowed(self) -> dict[str, object]:
        """Fail closed before a new tool or expansion after execution closes."""

        state = self.checkpoint()
        if state["phase"] == "expired":
            raise HardDeadlineReached("hard deadline reached")
        if state["phase"] == "reserve":
            raise NewWorkWindowClosed(
                "execution window closed; preserve the remaining time for verification and handoff"
            )
        return state

    def require_action_allowed(
        self,
        *,
        estimated_seconds: float = 0.0,
        optional: bool = False,
    ) -> dict[str, object]:
        """Reject work that cannot fit or conflicts with a scope-reduction action."""

        if not math.isfinite(estimated_seconds) or estimated_seconds < 0:
            raise ValueError("estimated_seconds must be finite and non-negative")
        state = self.require_new_work_allowed()
        if optional and state.get("forecast_status") == "missing":
            raise NewWorkWindowClosed(
                "optional work requires a current remaining-work forecast"
            )
        adjusted_duration = estimated_seconds * self.calibration_multiplier
        if adjusted_duration > float(state["execution_remaining_seconds"]):
            raise NewWorkWindowClosed(
                "action does not fit before the verification reserve"
            )
        action = state.get("action")
        if optional and action not in (None, "continue"):
            raise NewWorkWindowClosed(
                f"optional work is prohibited by control action {action}"
            )
        return state

    def model_context(self) -> str:
        """Return compact machine-derived state suitable for model input injection."""

        state = self.checkpoint()
        exposed = {
            key: state[key]
            for key in (
                "now",
                "deadline",
                "work_deadline",
                "phase",
                "remaining_seconds",
                "execution_remaining_seconds",
                "forecast_status",
                "remaining_work_interval",
                "calibration_multiplier",
                "feasibility",
                "action",
                "reason",
            )
            if key in state
        }
        return (
            "TIME_CONTROL_STATE="
            + json.dumps(exposed, ensure_ascii=False, separators=(",", ":"))
            + "\nObey stop and verify_and_handoff. Do not start optional work when the action restricts scope."
        )

    async def run_until_hard_deadline(self, awaitable: Awaitable[T]) -> T:
        """Cancel one local awaitable and return control when the deadline arrives.

        Python task cancellation is cooperative. This method stops waiting promptly,
        but a task that suppresses cancellation or a remote side effect may continue;
        use process isolation or a verified remote cancellation API when containment is
        required.
        """

        remaining = (self.contract.deadline - self._clock()).total_seconds()
        if remaining <= 0:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise HardDeadlineReached("hard deadline reached before run start")
        task = asyncio.ensure_future(awaitable)

        def drain(completed: asyncio.Future[T]) -> None:
            if not completed.cancelled():
                completed.exception()

        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(drain)
            raise
        if task in done:
            return await task
        task.cancel()
        await asyncio.sleep(0)
        task.add_done_callback(drain)
        raise HardDeadlineReached("agent run crossed its hard deadline")
