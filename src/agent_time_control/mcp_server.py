"""MCP adapter for Agent Time Control.

The server exposes clock and deterministic decision tools. It deliberately does
not expose arbitrary subprocess execution, file access, scheduling, or durable
history writes; those belong to an authorized host controller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server import MCPServer

from .calibration import summarize_records
from .core import build_snapshot, create_timebox, decide, parse_timestamp

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
    version="0.1.0",
)


def _now(timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone_name!r}") from exc
    return datetime.now(zone)


@mcp.tool(structured_output=True)
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
def start_timebox(
    duration_seconds: float,
    reserve_seconds: float = 0.0,
    timezone_name: str = "UTC",
) -> dict[str, object]:
    """Start one relative wall-clock timebox and return the fixed start and deadline."""

    return create_timebox(
        duration_seconds=duration_seconds,
        now=_now(timezone_name),
        reserve_seconds=reserve_seconds,
    )


@mcp.tool(structured_output=True)
def check_deadline(
    deadline: str,
    reserve_seconds: float = 0.0,
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
def evaluate_checkpoint(
    deadline: str,
    estimate_low_seconds: float,
    estimate_likely_seconds: float,
    estimate_high_seconds: float,
    reserve_seconds: float = 0.0,
    calibration_multiplier: float = 1.0,
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
def summarize_calibration(
    records: list[dict[str, Any]], task_class: str | None = None
) -> dict[str, object]:
    """Summarize caller-supplied comparable timing records without reading or storing files."""

    normalized: list[dict[str, object]] = [dict(record) for record in records]
    return summarize_records(normalized, task_class)


def main() -> None:
    """Run the local MCP server over stdio."""

    mcp.run("stdio")


if __name__ == "__main__":
    main()
