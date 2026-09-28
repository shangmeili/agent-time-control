"""Offline integration example: measure, forecast, gate, verify, and deliver.

No model/API is used here. Replace the operation bodies with authorized host work
and change reference_class when the workload, tools, model or host changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_time_control import TimeBudgetController, TimeContract, TimingRecorder


async def main() -> dict:
    recorder = TimingRecorder("offline-sha256-v1/local-host")
    payload = b"agent-time-control-example" * 1000
    expected = hashlib.sha256(payload).hexdigest()

    def verify() -> None:
        with recorder.measure("verification"):
            if hashlib.sha256(payload).hexdigest() != expected:
                raise RuntimeError("verification digest mismatch")

    for _ in range(5):
        verify()
    history = recorder.snapshot()
    controller = TimeBudgetController(TimeContract.relative(5, reserve_seconds=1))
    state = controller.update_forecast_from_history(
        ["verification"], history, reference_class=recorder.reference_class
    )
    estimate = state["remaining_work_interval"]["high_seconds"]
    controller.require_action_allowed(
        estimated_seconds=estimate, action_kind="verification"
    )

    async def work() -> str:
        verify()
        return expected

    result = await controller.run_until_hard_deadline(work())
    controller.invalidate_forecast()
    return {
        "verified": result == expected,
        "observations": len(recorder.snapshot()),
        "forecast_source": state["forecast_evidence"]["source"],
        "network_used": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), sort_keys=True))
