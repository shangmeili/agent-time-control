"""Deterministic time-budget primitives for agent runtimes."""

from .calibration import summarize_records
from .controller import (
    HardDeadlineReached,
    NewWorkWindowClosed,
    TimeBudgetController,
    TimeContract,
)
from .core import (
    build_snapshot,
    create_timebox,
    decide,
    parse_timestamp,
)

__all__ = [
    "HardDeadlineReached",
    "NewWorkWindowClosed",
    "TimeBudgetController",
    "TimeContract",
    "build_snapshot",
    "create_timebox",
    "decide",
    "parse_timestamp",
    "summarize_records",
]

__version__ = "0.1.0"
