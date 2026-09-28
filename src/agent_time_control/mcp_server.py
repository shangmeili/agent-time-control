"""MCP adapter for Agent Time Control.

The server exposes clock and deterministic decision tools. It deliberately does
not expose arbitrary subprocess execution, file access, scheduling, or durable
history writes; those belong to an authorized host controller.
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .calibration import summarize_records
from .core import build_snapshot, create_timebox, decide, parse_timestamp
from .forecasting import forecast_remaining_work

SERVER_INSTRUCTIONS = """
Use these tools as an external clock and deterministic time-budget controller.
Start a relative timebox once and retain its returned started_at and deadline.
Refresh the snapshot after uncertain or high-latency work and before expanding
scope or entering verification. Treat stop and verify_and_handoff as mandatory;
do not replace an adverse result with a more convenient unsupported estimate.
This service does not schedule wakeups or enforce cancellation by itself.
""".strip()


mcp = MCPServer(
    name="agent-time-control",
    title="Agent Time Control",
    description="External wall clock, timebox tracking, and deterministic budget gates for agents.",
    instructions=SERVER_INSTRUCTIONS,
    version=__version__,
)


Seconds = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


def _checked_input(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ToolError(str(exc)) from exc

    return checked


def _now(timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone_name!r}") from exc
    return datetime.now(zone)


@mcp.tool(structured_output=True)
@_checked_input
def time_now(timezone_name: str = "UTC") -> dict[str, object]:
    """Read the host wall clock in an IANA timezone; do not infer time from conversation."""

    observed = _now(timezone_name)
    return {
        "schema_version": "1.0",
        "clock_source": "host_system_clock",
        "timezone": timezone_name,
        "now": observed.isoformat(),
        "unix_seconds": observed.timestamp(),
    }


@mcp.tool(structured_output=True)
@_checked_input
def start_timebox(
    duration_seconds: Seconds,
    reserve_seconds: Seconds = 0.0,
    timezone_name: str = "UTC",
) -> dict[str, object]:
    """Start one relative wall-clock timebox and return the fixed start and deadline."""

    return create_timebox(
        duration_seconds=duration_seconds,
        now=_now(timezone_name),
        reserve_seconds=reserve_seconds,
    )


@mcp.tool(structured_output=True)
@_checked_input
def check_deadline(
    deadline: str,
    reserve_seconds: Seconds = 0.0,
    started_at: str | None = None,
) -> dict[str, object]:
    """Refresh remaining time for an absolute deadline with a timezone offset."""

    parsed_deadline = parse_timestamp(deadline)
    return build_snapshot(
        deadline=parsed_deadline,
        now=datetime.now(parsed_deadline.tzinfo),
        started_at=parse_timestamp(started_at) if started_at else None,
        reserve_seconds=reserve_seconds,
    )


@mcp.tool(structured_output=True)
@_checked_input
def evaluate_checkpoint(
    deadline: str,
    estimate_low_seconds: Seconds,
    estimate_likely_seconds: Seconds,
    estimate_high_seconds: Seconds,
    reserve_seconds: Seconds = 0.0,
    calibration_multiplier: Seconds = 1.0,
    started_at: str | None = None,
) -> dict[str, object]:
    """Compare a remaining-work range with the live execution window and return a control action."""

    parsed_deadline = parse_timestamp(deadline)
    snapshot = build_snapshot(
        deadline=parsed_deadline,
        now=datetime.now(parsed_deadline.tzinfo),
        started_at=parse_timestamp(started_at) if started_at else None,
        reserve_seconds=reserve_seconds,
    )
    return decide(
        snapshot,
        low_seconds=estimate_low_seconds,
        likely_seconds=estimate_likely_seconds,
        high_seconds=estimate_high_seconds,
        multiplier=calibration_multiplier,
    )


@mcp.tool(structured_output=True)
@_checked_input
def summarize_calibration(
    records: list[dict[str, Any]], task_class: str | None = None
) -> dict[str, object]:
    """Summarize caller-supplied comparable timing records without reading or storing files."""

    normalized: list[dict[str, object]] = [dict(record) for record in records]
    return summarize_records(normalized, task_class)


@mcp.tool(structured_output=True)
@_checked_input
def forecast_remaining(
    remaining_steps: list[str],
    observations: list[dict[str, Any]],
    reference_class: str,
) -> dict[str, Any]:
    """Estimate an explicit serial plan from caller-supplied past step timings.

    Include model/tool/queue/handoff calls. Use the same reference_class only for
    comparable model, tools, host and workload. Insufficient or censored history
    returns no finite forecast; observed ranges are not calibrated probabilities.
    """
    return forecast_remaining_work(
        remaining_steps, observations, reference_class=reference_class
    )


def main() -> None:
    """Run the local MCP server over stdio."""

    mcp.run("stdio")


if __name__ == "__main__":
    main()
