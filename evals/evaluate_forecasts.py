"""Prospective held-out comparison of raw, grounded and empirical duration estimates.

Uses an already running, authorized Ollama server and never downloads models.
Calibration precedes holdout; held-out outcomes never enter the timing history.
All three estimates refer to the same later serial model/verification/handoff run.
Forecast-query latency is recorded separately and is outside that target.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_ollama import (
    estimation_prompt,
    installed_models,
    parse_remaining_work_estimate,
)

from agent_time_control.forecasting import forecast_remaining_work

PROFILES = {"fast": 0.1, "medium": 0.4, "slow": 1.2}
STEPS = ["model_decision", "verify", "handoff"]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for method in ("original_prompt", "grounded_prompt", "empirical_steps"):
        errors, widths, covered, costs = [], [], [], []
        invalid = 0
        for record in records:
            prediction = record["predictions"][method]
            costs.append(prediction["forecast_cost_seconds"])
            interval = prediction.get("interval")
            if interval is None:
                invalid += 1
                continue
            if record["outcome"] != "complete":
                continue
            low, likely, high = interval
            actual = record["actual_remaining_seconds"]
            errors.append(abs(likely - actual))
            widths.append(high - low)
            covered.append(low <= actual <= high)
        result[method] = {
            "records": len(records),
            "scored_completed": len(errors),
            "invalid_forecasts": invalid,
            "median_absolute_error_seconds": median(errors) if errors else None,
            "mean_absolute_error_seconds": mean(errors) if errors else None,
            "interval_coverage": mean(covered) if covered else None,
            "median_interval_width_seconds": median(widths) if widths else None,
            "mean_forecast_cost_seconds": mean(costs),
        }
    return result


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    from openai import AsyncOpenAI

    from agents import (
        Agent,
        ModelSettings,
        OpenAIChatCompletionsModel,
        RunConfig,
        Runner,
    )

    if args.model not in installed_models(args.host):
        raise ValueError("model must already be installed")
    if args.calibration < 5 or args.holdout < 1:
        raise ValueError(
            "calibration must be >=5 per profile and holdout must be positive"
        )
    client = AsyncOpenAI(
        base_url=args.host.rstrip("/") + "/v1", api_key="ollama-local", max_retries=0
    )
    model = OpenAIChatCompletionsModel(model=args.model, openai_client=client)
    config = RunConfig(tracing_disabled=True)
    rng = random.Random(args.seed)
    calibration, history, holdout = [], [], []
    protocol = {
        "model": args.model,
        "seed": args.seed,
        "profiles": PROFILES,
        "calibration_per_profile": args.calibration,
        "holdout_per_profile": args.holdout,
        "target": "one model decision, one verification, host handoff after all forecasts return",
        "forecast_cost_in_target": False,
        "history_frozen_before_holdout": True,
        "nominal_verification_jitter": [0.8, 1.2],
        "checks": {
            "empirical_median_error_ratio_max": 0.5,
            "empirical_coverage_min": 0.8,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    async def query(prompt: str, seed: int) -> str:
        agent = Agent(
            name="duration-evaluation",
            instructions="Follow the output format exactly.",
            model=model,
            model_settings=ModelSettings(temperature=0, extra_args={"seed": seed}),
        )
        result = await asyncio.wait_for(
            Runner.run(agent, prompt, run_config=config, max_turns=1), timeout=15
        )
        return str(result.final_output or "")

    async def pipeline(profile: str, run_id: str, seed: int) -> dict[str, Any]:
        reference = f"{args.model}|{args.host}|serial-v1|{profile}"
        events = []
        started = time.monotonic()
        last = started
        outcome, error = "complete", None
        current = "model_decision"
        try:
            raw = await query(
                "The core fact is already obtained. Reply exactly verify_core_fact.",
                seed,
            )
            now = time.monotonic()
            events.append(
                {
                    "reference_class": reference,
                    "operation": current,
                    "elapsed_seconds": now - last,
                    "outcome": "complete",
                    "run_id": run_id,
                }
            )
            if "verify_core_fact" not in raw:
                raise ValueError(f"unexpected decision: {raw!r}")
            last, current = now, "verify"
            await asyncio.sleep(PROFILES[profile] * rng.uniform(0.8, 1.2))
            now = time.monotonic()
            events.append(
                {
                    "reference_class": reference,
                    "operation": current,
                    "elapsed_seconds": now - last,
                    "outcome": "complete",
                    "run_id": run_id,
                }
            )
            last, current = now, "handoff"
            json.dumps({"verified": True, "run_id": run_id})
            await asyncio.sleep(0)
            now = time.monotonic()
            events.append(
                {
                    "reference_class": reference,
                    "operation": current,
                    "elapsed_seconds": now - last,
                    "outcome": "complete",
                    "run_id": run_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - retain every failed evaluation run
            outcome = "timed_out" if isinstance(exc, TimeoutError) else "failed"
            error = f"{type(exc).__name__}: {exc}"
            events.append(
                {
                    "reference_class": reference,
                    "operation": current,
                    "elapsed_seconds": time.monotonic() - last,
                    "outcome": outcome,
                    "run_id": run_id,
                }
            )
        return {
            "run_id": run_id,
            "profile": profile,
            "reference_class": reference,
            "outcome": outcome,
            "error": error,
            "actual_remaining_seconds": time.monotonic() - started,
            "observations": events,
        }

    try:
        # Warm-up is excluded from calibration and holdout.
        await query("Reply exactly OK.", args.seed)
        with args.output.open("x", encoding="utf-8") as handle:

            def retain(value):
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")
                handle.flush()

            retain({"phase": "protocol", **protocol})
            jobs = [(p, r) for p in PROFILES for r in range(args.calibration)]
            rng.shuffle(jobs)
            for index, (profile, repetition) in enumerate(jobs):
                record = await pipeline(
                    profile, f"cal-{profile}-{repetition}", args.seed + index
                )
                calibration.append(record)
                history.extend(record["observations"])
                retain({"phase": "calibration", **record})
            frozen_hash = digest(history)
            print(
                f"CALIBRATION retained={len(calibration)} history_sha256={frozen_hash}",
                flush=True,
            )
            jobs = [(p, r) for p in PROFILES for r in range(args.holdout)]
            rng.shuffle(jobs)
            for index, (profile, repetition) in enumerate(jobs):
                reference = f"{args.model}|{args.host}|serial-v1|{profile}"
                seed = args.seed + 10000 + index
                predictions = {}
                t0 = time.monotonic()
                empirical = forecast_remaining_work(
                    STEPS, history, reference_class=reference
                )
                interval = empirical["remaining_work_interval"]
                predictions["empirical_steps"] = {
                    "interval": [
                        interval[key]
                        for key in ("low_seconds", "likely_seconds", "high_seconds")
                    ]
                    if interval
                    else None,
                    "forecast_cost_seconds": time.monotonic() - t0,
                    "evidence": empirical,
                }
                prompts = {
                    "original_prompt": (
                        "Resolve this task within 20 seconds of wall-clock time. Obtain the required fact, "
                        "verify it, then hand off. Estimate the remaining workflow duration in seconds. "
                        "Return exactly three numbers: low likely high with 0 <= low <= likely <= high."
                    ),
                    "grounded_prompt": estimation_prompt(
                        completed_steps=[{"operation": "core", "status": "complete"}],
                        required_steps=["verify"],
                        optional_steps=[],
                        nominal_step_seconds={"verify": PROFILES[profile]},
                        model_call_seconds=[
                            r["elapsed_seconds"]
                            for r in history
                            if r["reference_class"] == reference
                            and r["operation"] == "model_decision"
                            and r["outcome"] == "complete"
                        ],
                    ),
                }
                order = list(prompts)
                rng.shuffle(order)
                for method in order:
                    t0 = time.monotonic()
                    raw, error, interval = "", None, None
                    try:
                        raw = await query(prompts[method], seed)
                        interval = list(parse_remaining_work_estimate(raw))
                    except Exception as exc:  # noqa: BLE001 - retain invalid predictions
                        error = f"{type(exc).__name__}: {exc}"
                    predictions[method] = {
                        "interval": interval,
                        "raw": raw,
                        "error": error,
                        "forecast_cost_seconds": time.monotonic() - t0,
                    }
                issued_at = time.time()
                record = await pipeline(
                    profile, f"holdout-{profile}-{repetition}", seed
                )
                record.update(
                    predictions=predictions,
                    forecasts_issued_at=issued_at,
                    history_sha256=frozen_hash,
                )
                holdout.append(record)
                retain({"phase": "holdout", **record})
                assert digest(history) == frozen_hash, (
                    "held-out outcomes changed calibration history"
                )
                print(
                    f"HOLDOUT {index + 1}/{len(jobs)} {profile} {record['outcome']}",
                    flush=True,
                )
        metrics = summarize(holdout)
        baseline = metrics["original_prompt"]["median_absolute_error_seconds"]
        empirical = metrics["empirical_steps"]
        report = {
            "protocol": protocol,
            "history_sha256": frozen_hash,
            "calibration_runs": len(calibration),
            "holdout_runs": len(holdout),
            "failed_calibration_runs": sum(
                r["outcome"] != "complete" for r in calibration
            ),
            "failed_holdout_runs": sum(r["outcome"] != "complete" for r in holdout),
            "metrics": metrics,
            "checks": {
                "all_holdouts_complete": all(
                    r["outcome"] == "complete" for r in holdout
                ),
                "no_invalid_empirical_forecasts": empirical["invalid_forecasts"] == 0,
                "empirical_error_at_most_half_original": baseline is not None
                and empirical["median_absolute_error_seconds"] is not None
                and empirical["median_absolute_error_seconds"] <= baseline * 0.5,
                "empirical_coverage_at_least_80_percent": empirical["interval_coverage"]
                is not None
                and empirical["interval_coverage"] >= 0.8,
            },
            "interpretation": "descriptive held-out synthetic workload evidence, not cross-model calibration",
        }
        report["passed"] = all(report["checks"].values())
        return report
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=int, default=12)
    parser.add_argument("--holdout", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260929)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    report_path = args.output.with_suffix(".report.json")
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
