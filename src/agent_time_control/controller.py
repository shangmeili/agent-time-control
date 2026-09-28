"""Host-side controller for automatic checkpoints and hard run limits."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, TypeVar

from .core import (
    _finite_nonnegative,
    _require_aware,
    build_snapshot,
    decide,
    parse_timestamp,
)
from .forecasting import forecast_remaining_work

T = TypeVar("T")
ActionKind = Literal["work", "verification", "handoff"]


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
            _require_aware(value, field)
        _finite_nonnegative(self.reserve_seconds, "reserve_seconds")
        if not isinstance(self.clock_source, str) or not self.clock_source.strip():
            raise ValueError("clock_source must be non-empty")
        started_utc = self.started_at.astimezone(timezone.utc)
        deadline_utc = self.deadline.astimezone(timezone.utc)
        if deadline_utc <= started_utc:
            raise ValueError("deadline must be later than started_at")
        total = (deadline_utc - started_utc).total_seconds()
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
        duration_seconds = _finite_nonnegative(duration_seconds, "duration_seconds")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("now must include timezone information")
        try:
            deadline = (
                observed.astimezone(timezone.utc) + timedelta(seconds=duration_seconds)
            ).astimezone(observed.tzinfo)
        except OverflowError as exc:
            raise ValueError("duration exceeds the supported datetime range") from exc
        return cls(
            started_at=observed,
            deadline=deadline,
            reserve_seconds=reserve_seconds,
        )


class TimeBudgetController:
    """Refresh time state automatically and turn forecasts into control actions."""

    def __init__(
        self,
        contract: TimeContract,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        calibration_multiplier: float = 1.0,
    ) -> None:
        calibration_multiplier = _finite_nonnegative(
            calibration_multiplier, "calibration_multiplier"
        )
        if not math.isfinite(calibration_multiplier) or calibration_multiplier <= 0:
            raise ValueError("calibration_multiplier must be positive")
        self.contract = contract
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Custom test clocks stay deterministic unless paired with an elapsed clock.
        self._monotonic_clock = monotonic_clock or (
            time.monotonic if clock is None else None
        )
        anchor = self._clock()
        _require_aware(anchor, "clock")
        self._clock_anchor = anchor.astimezone(timezone.utc)
        self._last_observed = self._clock_anchor
        self._monotonic_anchor = (
            self._monotonic_clock() if self._monotonic_clock else None
        )
        self.calibration_multiplier = calibration_multiplier
        self._forecast: tuple[float, float, float] | None = None
        self._forecast_evidence: dict[str, object] | None = None

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
        self._forecast_evidence = None
        return state

    def update_forecast_from_history(
        self,
        remaining_steps: list[str],
        observations: list[dict[str, object]],
        *,
        reference_class: str,
    ) -> dict[str, object]:
        """Replace a forecast from past comparable step timings and an explicit plan.

        Resupply the remaining plan after progress or scope changes. Insufficient
        evidence invalidates the prior plan's forecast instead of reusing it.
        """
        evidence = forecast_remaining_work(
            remaining_steps, observations, reference_class=reference_class
        )
        interval = evidence["remaining_work_interval"]
        if interval is None:
            self._forecast = None
        else:
            self.update_forecast(
                interval["low_seconds"],
                interval["likely_seconds"],
                interval["high_seconds"],
            )
        self._forecast_evidence = evidence
        return self.checkpoint()

    def _observe_time(self) -> datetime:
        observed = self._clock()
        _require_aware(observed, "clock")
        observed = observed.astimezone(timezone.utc)
        if self._monotonic_clock is not None:
            elapsed = self._monotonic_clock() - self._monotonic_anchor
            elapsed = _finite_nonnegative(elapsed, "monotonic elapsed time")
            observed = max(observed, self._clock_anchor + timedelta(seconds=elapsed))
        self._last_observed = max(observed, self._last_observed)
        return self._last_observed

    def invalidate_forecast(self) -> None:
        """Discard a plan's estimate after work or scope changes."""
        self._forecast = None
        self._forecast_evidence = None

    def snapshot(self) -> dict[str, object]:
        observed = self._observe_time()
        return build_snapshot(
            deadline=self.contract.deadline,
            now=observed,
            started_at=self.contract.started_at,
            reserve_seconds=self.contract.reserve_seconds,
            clock_source=self.contract.clock_source,
        )

    def checkpoint(self) -> dict[str, object]:
        snapshot = self.snapshot()
        if self._forecast_evidence is not None:
            snapshot["forecast_evidence"] = deepcopy(self._forecast_evidence)
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
        action_kind: ActionKind = "work",
    ) -> dict[str, object]:
        """Gate work before reserve, or host-designated verification/handoff before expiry."""

        if action_kind not in ("work", "verification", "handoff"):
            raise ValueError("action_kind must be work, verification, or handoff")
        if optional and action_kind != "work":
            raise ValueError("optional work cannot use the verification reserve")
        estimated_seconds = _finite_nonnegative(estimated_seconds, "estimated_seconds")
        if action_kind == "work":
            state = self.require_new_work_allowed()
            available = (
                parse_timestamp(state["work_deadline"]) - parse_timestamp(state["now"])
            ).total_seconds()
            boundary = "verification reserve"
        else:
            state = self.checkpoint()
            if state["phase"] == "expired":
                raise HardDeadlineReached("hard deadline reached")
            available = (
                parse_timestamp(state["deadline"]) - parse_timestamp(state["now"])
            ).total_seconds()
            boundary = "hard deadline"
        if optional and state.get("forecast_status") == "missing":
            raise NewWorkWindowClosed(
                "optional work requires a current remaining-work forecast"
            )
        adjusted_duration = estimated_seconds * self.calibration_multiplier
        if adjusted_duration > available:
            raise NewWorkWindowClosed(f"action does not fit before the {boundary}")
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
                "forecast_evidence",
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

        def drain(completed: asyncio.Future[T]) -> None:
            if not completed.cancelled():
                completed.exception()

        def dispose() -> None:
            if asyncio.isfuture(awaitable):
                awaitable.cancel()
                awaitable.add_done_callback(drain)
            elif inspect.iscoroutine(awaitable):
                awaitable.close()

        try:
            remaining = (
                self.contract.deadline.astimezone(timezone.utc) - self._observe_time()
            ).total_seconds()
        except BaseException:
            dispose()
            raise
        if remaining <= 0:
            dispose()
            raise HardDeadlineReached("hard deadline reached before run start")
        task = asyncio.ensure_future(awaitable)

        try:
            while remaining > 0:
                done, _ = await asyncio.wait({task}, timeout=min(remaining, 0.25))
                if task in done:
                    return await task
                remaining = (
                    self.contract.deadline.astimezone(timezone.utc)
                    - self._observe_time()
                ).total_seconds()
        except BaseException:
            task.cancel()
            task.add_done_callback(drain)
            raise
        task.cancel()
        await asyncio.sleep(0)
        task.add_done_callback(drain)
        raise HardDeadlineReached("agent run crossed its hard deadline")
