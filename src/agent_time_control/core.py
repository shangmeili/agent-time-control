"""Pure time-contract and budget-gate logic.

The functions in this module do not depend on MCP or on a particular agent SDK.
They accept an observed clock value so tests and host adapters can be deterministic.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp that identifies an exact instant."""

    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset or Z timezone marker")
    return parsed


def _finite_nonnegative(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")


def _conservative_seconds(later: datetime, earlier: datetime) -> int:
    """Return whole seconds without ever overstating the remaining budget."""

    return math.floor((later - earlier).total_seconds())


def build_snapshot(
    *,
    deadline: datetime,
    now: datetime,
    started_at: datetime | None = None,
    reserve_seconds: float = 0.0,
    clock_source: str = "host_system_clock",
) -> dict[str, object]:
    """Build a conservative wall-clock snapshot for an absolute deadline."""

    _require_aware(deadline, "deadline")
    _require_aware(now, "now")
    if started_at is not None:
        _require_aware(started_at, "started_at")
    reserve_value = _finite_nonnegative(reserve_seconds, "reserve_seconds")
    if not clock_source:
        raise ValueError("clock_source must be non-empty")

    reserve = timedelta(seconds=reserve_value)
    work_deadline = deadline - reserve
    raw_remaining = (deadline - now).total_seconds()
    raw_execution_remaining = (work_deadline - now).total_seconds()
    remaining = _conservative_seconds(deadline, now)
    execution_remaining = _conservative_seconds(work_deadline, now)

    if raw_remaining <= 0:
        phase = "expired"
    elif raw_execution_remaining <= 0:
        phase = "reserve"
    else:
        phase = "execute"

    payload: dict[str, object] = {
        "schema_version": "1.0",
        "clock_source": clock_source,
        "now": now.isoformat(),
        "deadline": deadline.isoformat(),
        "work_deadline": work_deadline.isoformat(),
        "phase": phase,
        "expired": raw_remaining <= 0,
        "remaining_seconds": remaining,
        "execution_remaining_seconds": execution_remaining,
        "reserve_seconds": math.floor(reserve_value),
    }

    if started_at is not None:
        total_budget = _conservative_seconds(deadline, started_at)
        elapsed = math.floor((now - started_at).total_seconds())
        payload.update(
            {
                "started_at": started_at.isoformat(),
                "elapsed_seconds": elapsed,
                "total_budget_seconds": total_budget,
                "used_fraction": elapsed / total_budget if total_budget > 0 else None,
            }
        )
    return payload


def create_timebox(
    *,
    duration_seconds: float,
    now: datetime,
    reserve_seconds: float = 0.0,
    clock_source: str = "host_system_clock",
) -> dict[str, object]:
    """Create a relative timebox anchored to one recorded start instant."""

    _require_aware(now, "now")
    duration = _finite_nonnegative(duration_seconds, "duration_seconds")
    reserve = _finite_nonnegative(reserve_seconds, "reserve_seconds")
    if duration <= 0:
        raise ValueError("duration_seconds must be positive")
    if reserve > duration:
        raise ValueError("reserve_seconds must not exceed duration_seconds")
    deadline = now + timedelta(seconds=duration)
    return build_snapshot(
        deadline=deadline,
        now=now,
        started_at=now,
        reserve_seconds=reserve,
        clock_source=clock_source,
    )


def decide(
    snapshot: dict[str, object],
    *,
    low_seconds: float,
    likely_seconds: float,
    high_seconds: float,
    multiplier: float = 1.0,
) -> dict[str, object]:
    """Apply the deterministic control gate to a remaining-work interval."""

    low = _finite_nonnegative(low_seconds, "low_seconds")
    likely = _finite_nonnegative(likely_seconds, "likely_seconds")
    high = _finite_nonnegative(high_seconds, "high_seconds")
    multiplier_value = _finite_nonnegative(multiplier, "multiplier")
    if multiplier_value <= 0:
        raise ValueError("multiplier must be positive")
    if not low <= likely <= high:
        raise ValueError("remaining-work estimates must satisfy low <= likely <= high")

    adjusted = {
        "low_seconds": low * multiplier_value,
        "likely_seconds": likely * multiplier_value,
        "high_seconds": high * multiplier_value,
    }
    try:
        execution_remaining = int(snapshot["execution_remaining_seconds"])
        phase = snapshot["phase"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("snapshot is missing valid phase or execution budget") from exc

    if phase == "expired":
        feasibility = "infeasible"
        action = "stop"
        reason = "hard deadline reached"
    elif phase == "reserve":
        feasibility = "infeasible_for_new_work"
        action = "verify_and_handoff"
        reason = "execution window exhausted; reserve is active"
    elif phase != "execute":
        raise ValueError(f"unsupported snapshot phase: {phase!r}")
    elif adjusted["low_seconds"] > execution_remaining:
        feasibility = "infeasible"
        action = "reduce_scope_or_handoff"
        reason = "even the lower remaining-work estimate exceeds the execution window"
    elif adjusted["likely_seconds"] > execution_remaining:
        feasibility = "unlikely"
        action = "replan_and_reduce_scope"
        reason = "the likely remaining-work estimate exceeds the execution window"
    elif adjusted["high_seconds"] > execution_remaining:
        feasibility = "at_risk"
        action = "continue_core_only"
        reason = "the upper remaining-work estimate exceeds the execution window"
    else:
        feasibility = "feasible"
        action = "continue"
        reason = "the full adjusted interval fits inside the execution window"

    return {
        **snapshot,
        "remaining_work_interval": adjusted,
        "calibration_multiplier": multiplier_value,
        "feasibility": feasibility,
        "action": action,
        "reason": reason,
    }
