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
from .forecasting import forecast_remaining_work
from .observations import TimingRecorder

__all__ = [
    "HardDeadlineReached",
    "NewWorkWindowClosed",
    "TimeBudgetController",
    "TimeContract",
    "TimingRecorder",
    "build_snapshot",
    "create_timebox",
    "decide",
    "forecast_remaining_work",
    "parse_timestamp",
    "summarize_records",
]

__version__ = "0.2.0"
