"""Evidence-based duration scenarios for an explicitly supplied serial work plan.

The caller owns the plan and reference-class key (model, tools, workload and host).
Only supply observations available before this prediction. This module neither
reads files nor learns from the future outcome. Empirical extrema are scenarios,
not a calibrated prediction interval or a hard completion guarantee.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Any

MINIMUM_STEP_SAMPLES = 5
MAX_OBSERVATIONS = 10000
MAX_REMAINING_STEPS = 1000
OUTCOMES = {"complete", "partial", "failed", "timed_out"}


def forecast_remaining_work(
    remaining_steps: list[str],
    observations: list[dict[str, Any]],
    *,
    reference_class: str,
) -> dict[str, Any]:
    """Sum observed low/median/high durations for the remaining serial operations.

    Repeated step names count repeated invocations. Include model calls, queues,
    retries and handoff when they are part of the plan. Unknown work is not zero.
    Five observations is an evidence floor, not a statistical calibration claim.
    Censored/failed observations block a success-only finite completion forecast.
    """

    if not isinstance(reference_class, str) or not reference_class.strip():
        raise ValueError(
            "reference_class must identify comparable model, tools and host"
        )
    if not isinstance(remaining_steps, list) or any(
        not isinstance(step, str) or not step.strip() for step in remaining_steps
    ):
        raise ValueError("remaining_steps must be a list of non-empty operation names")
    if not isinstance(observations, list):
        raise TypeError("observations must be an array")
    if (
        len(observations) > MAX_OBSERVATIONS
        or len(remaining_steps) > MAX_REMAINING_STEPS
    ):
        raise ValueError("request exceeds 10000 observations or 1000 remaining steps")
    seen_ids: set[str] = set()
    counts = Counter(remaining_steps)
    samples: dict[str, list[float]] = {step: [] for step in counts}
    censored = dict.fromkeys(counts, 0)
    for index, record in enumerate(observations):
        if not isinstance(record, dict):
            raise TypeError(f"observation {index}: expected an object")
        observation_id = record.get("observation_id")
        if observation_id is not None:
            if (
                not isinstance(observation_id, str)
                or not observation_id
                or len(observation_id) > 128
            ):
                raise ValueError(f"observation {index}: invalid observation_id")
            if observation_id in seen_ids:
                raise ValueError(f"observation {index}: duplicate observation_id")
            seen_ids.add(observation_id)
        for field in ("reference_class", "operation"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError(f"observation {index}: {field} must be non-empty")
        value = record.get("elapsed_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"observation {index}: elapsed_seconds must be numeric")
        try:
            value = float(value)
        except OverflowError as exc:
            raise ValueError(
                f"observation {index}: elapsed_seconds out of range"
            ) from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"observation {index}: elapsed_seconds must be finite and non-negative"
            )
        if record.get("outcome") not in OUTCOMES:
            raise ValueError(f"observation {index}: invalid outcome")
        step = record["operation"]
        if record["reference_class"] != reference_class or step not in counts:
            continue
        if record["outcome"] == "complete":
            samples[step].append(value)
        else:
            censored[step] += 1

    missing = sorted(
        step for step in counts if len(samples[step]) < MINIMUM_STEP_SAMPLES
    )
    blocked = sorted(step for step in counts if censored[step])
    interval = None
    if not missing and not blocked:
        interval = {
            "low_seconds": math.fsum(
                min(samples[step]) * count for step, count in counts.items()
            ),
            "likely_seconds": math.fsum(
                median(samples[step]) * count for step, count in counts.items()
            ),
            "high_seconds": math.fsum(
                max(samples[step]) * count for step, count in counts.items()
            ),
        }
        if not all(math.isfinite(value) for value in interval.values()):
            raise ValueError("aggregate remaining duration is not finite")
    return {
        "status": "estimated" if interval is not None else "insufficient_evidence",
        "source": "observed_step_durations",
        "reference_class": reference_class,
        "remaining_steps": list(remaining_steps),
        "sample_counts": {step: len(values) for step, values in samples.items()},
        "censored_counts": censored,
        "missing_operations": missing,
        "censored_operations": blocked,
        "remaining_work_interval": interval,
        "uncertainty_status": "empirical_range_not_calibrated",
        "interval_method": "sum_observed_min_median_max",
        "assumptions": "serial plan; unchanged model, tools, host and workload; no unlisted work",
    }
