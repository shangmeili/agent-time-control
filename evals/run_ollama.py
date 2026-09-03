#!/usr/bin/env python3
"""Run matched time-control conditions against one already-installed Ollama model."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_time_control.controller import (
    HardDeadlineReached,
    TimeBudgetController,
    TimeContract,
)
from agent_time_control.evaluation import evaluate_pilot_advancement

CONDITIONS = ("base", "rules", "tracked", "controller")


@dataclass(frozen=True)
class BenchmarkCase:
    task_id: str
    core_fact: str
    optional_fact: str
    verification_receipt: str
    core_delay_factor: float
    optional_delay_factor: float
    verify_delay_factor: float


CASES = (
    BenchmarkCase(
        "incident-alpha",
        "CORE_ALPHA_PORT_4317",
        "OPTIONAL_ALPHA_REGION_EAST",
        "VERIFIED_ALPHA_9C2",
        1.0,
        1.0,
        1.0,
    ),
    BenchmarkCase(
        "incident-beta",
        "CORE_BETA_QUEUE_12",
        "OPTIONAL_BETA_OWNER_SRE",
        "VERIFIED_BETA_4F8",
        4.0,
        0.25,
        1.0,
    ),
    BenchmarkCase(
        "incident-gamma",
        "CORE_GAMMA_FLAG_SAFE",
        "OPTIONAL_GAMMA_BUILD_73",
        "VERIFIED_GAMMA_1D6",
        1.0,
        0.25,
        4.0,
    ),
)


TIME_RULES = """
Treat wall-clock time as external state. Preserve the required core and verification
before optional breadth. Use report_checkpoint with a fresh low/likely/high
remaining-work estimate after obtaining the core and before optional work. If the
time state restricts scope, stop exploration and finish or hand off honestly.
""".strip()


def installed_models(host: str) -> list[str]:
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot query Ollama at {url}: {exc}") from exc
    return sorted(
        model.get("name", "")
        for model in payload.get("models", [])
        if isinstance(model, dict) and model.get("name")
    )


def score_output(case: BenchmarkCase, output: str) -> float:
    points = 0
    if case.core_fact in output:
        points += 6
    if case.verification_receipt in output:
        points += 3
    if case.optional_fact in output:
        points += 1
    return points / 10


def user_prompt(case: BenchmarkCase, budget_seconds: float) -> str:
    return f"""
Resolve task {case.task_id} within {budget_seconds:.1f} seconds of wall-clock time.

Required acceptance criteria:
- obtain the hidden required fact with get_core_fact;
- call verify_core_fact after the required fact has been obtained;
- once verification succeeds, the host constructs the final result from observed
  tool state.

Optional: get_optional_fact can improve the answer, but it must not endanger the
required fact, verification, or final response. The report_checkpoint tool can
read the live budget and evaluate a low/likely/high remaining-work estimate.
""".strip()


def paired_sample_seed(base_seed: int, case: BenchmarkCase, repetition: int) -> int:
    """Return one stable seed shared by every condition in a matched block."""

    case_index = CASES.index(case)
    return base_seed + repetition * len(CASES) + case_index


def effective_delays(
    case: BenchmarkCase,
    core_delay: float,
    optional_delay: float,
    verify_delay: float,
) -> tuple[float, float, float]:
    return (
        core_delay * case.core_delay_factor,
        optional_delay * case.optional_delay_factor,
        verify_delay * case.verify_delay_factor,
    )


async def warm_model(host: str, model: str) -> None:
    url = host.rstrip("/") + "/api/chat"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode()

    def request() -> None:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            json.load(response)

    await asyncio.to_thread(request)


async def run_case(
    *,
    case: BenchmarkCase,
    condition: str,
    model_name: str,
    host: str,
    budget_seconds: float,
    reserve_seconds: float,
    core_delay: float,
    optional_delay: float,
    verify_delay: float,
    max_turns: int,
    sample_seed: int,
) -> dict[str, Any]:
    from openai import AsyncOpenAI

    from agent_time_control.adapters.openai_agents import (
        TimeBudgetHooks,
        make_call_model_input_filter,
        make_tool_input_guardrail,
    )
    from agents import (
        Agent,
        ModelSettings,
        OpenAIChatCompletionsModel,
        RunConfig,
        Runner,
        ToolExecutionConfig,
        function_tool,
        set_tracing_disabled,
    )

    set_tracing_disabled(True)
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    controller = TimeBudgetController(
        TimeContract.relative(
            budget_seconds,
            reserve_seconds=reserve_seconds,
            now=started_wall,
        )
    )
    tool_events: list[dict[str, Any]] = []
    decision_events: list[dict[str, Any]] = []
    rejected_tool_events: list[dict[str, Any]] = []
    raw_checkpoints: list[dict[str, float]] = []
    first_warning: float | None = None
    workflow = {
        "core_obtained": False,
        "optional_closed": False,
        "optional_obtained": False,
        "checkpoint_reported": False,
        "required_blocked": False,
        "verified": False,
    }
    case_core_delay, case_optional_delay, case_verify_delay = effective_delays(
        case,
        core_delay,
        optional_delay,
        verify_delay,
    )

    def elapsed() -> float:
        return time.monotonic() - started_monotonic

    def guardrail(
        tool_name: str,
        estimated_seconds: float,
        *,
        optional: bool = False,
    ):
        if condition != "controller":
            return []

        def record_rejection(reason: str) -> None:
            rejected_tool_events.append(
                {"tool": tool_name, "elapsed_seconds": elapsed(), "reason": reason}
            )
            if tool_name == "get_optional_fact":
                workflow["optional_closed"] = True
            elif tool_name in {"get_core_fact", "verify_core_fact"}:
                workflow["required_blocked"] = True

        return [
            make_tool_input_guardrail(
                controller,
                estimated_seconds=estimated_seconds,
                optional=optional,
                name=f"time_budget_{tool_name}",
                on_reject=record_rejection,
            )
        ]

    @function_tool(
        tool_input_guardrails=guardrail("get_core_fact", case_core_delay),
        timeout=max(case_core_delay * 3, 1.0),
        is_enabled=lambda *_: not workflow["core_obtained"],
    )
    async def get_core_fact() -> str:
        """Retrieve the fact required by the acceptance criteria."""

        tool_events.append({"tool": "get_core_fact", "started": elapsed()})
        await asyncio.sleep(case_core_delay)
        tool_events[-1]["finished"] = elapsed()
        workflow["core_obtained"] = True
        return (
            f"WORKFLOW_STATE core_obtained=true verified=false; "
            f"core_fact={case.core_fact}; continue with an enabled tool"
        )

    @function_tool(
        tool_input_guardrails=guardrail(
            "get_optional_fact", case_optional_delay, optional=True
        ),
        timeout=max(case_optional_delay * 3, 1.0),
        is_enabled=lambda *_: (
            workflow["core_obtained"]
            and not workflow["verified"]
            and not workflow["optional_closed"]
        ),
    )
    async def get_optional_fact() -> str:
        """Retrieve optional enrichment that is not required for acceptance."""

        tool_events.append({"tool": "get_optional_fact", "started": elapsed()})
        await asyncio.sleep(case_optional_delay)
        tool_events[-1]["finished"] = elapsed()
        workflow["optional_obtained"] = True
        workflow["optional_closed"] = True
        return (
            f"WORKFLOW_STATE optional_obtained=true verified=false; "
            f"optional_fact={case.optional_fact}; continue with an enabled tool"
        )

    @function_tool(
        tool_input_guardrails=guardrail("verify_core_fact", case_verify_delay),
        timeout=max(case_verify_delay * 3, 1.0),
        is_enabled=lambda *_: workflow["core_obtained"] and not workflow["verified"],
    )
    async def verify_core_fact() -> str:
        """Verify the required fact already retained by the host workflow."""

        tool_events.append({"tool": "verify_core_fact", "started": elapsed()})
        await asyncio.sleep(case_verify_delay)
        tool_events[-1]["finished"] = elapsed()
        workflow["verified"] = True
        return (
            f"WORKFLOW_STATE verified=true; "
            f"verification_receipt={case.verification_receipt}; call submit_result"
        )

    @function_tool(
        is_enabled=lambda *_: (
            workflow["core_obtained"]
            and not workflow["verified"]
            and not workflow["checkpoint_reported"]
        )
    )
    def report_checkpoint(
        low_seconds: float,
        likely_seconds: float,
        high_seconds: float,
    ) -> str:
        """Report a progressive remaining-work range and receive the live budget action."""

        nonlocal first_warning
        observed_elapsed = elapsed()
        event: dict[str, Any] = {
            "tool": "report_checkpoint",
            "started": observed_elapsed,
        }
        try:
            state = controller.update_forecast(
                low_seconds,
                likely_seconds,
                high_seconds,
            )
        except ValueError as exc:
            event["finished"] = elapsed()
            event["error"] = str(exc)
            tool_events.append(event)
            return f"INVALID_CHECKPOINT: {exc}"
        raw_checkpoints.append(
            {
                "observed_elapsed_seconds": observed_elapsed,
                "estimate_low_seconds": low_seconds,
                "estimate_likely_seconds": likely_seconds,
                "estimate_high_seconds": high_seconds,
            }
        )
        workflow["checkpoint_reported"] = True
        event["finished"] = elapsed()
        tool_events.append(event)
        if state.get("action") not in (None, "continue") and first_warning is None:
            first_warning = observed_elapsed
        return controller.model_context()

    def host_result() -> str:
        """Build the result only from host-observed workflow state."""

        if not workflow["core_obtained"]:
            return ""
        output_event = "submit_result" if workflow["verified"] else "handoff_result"
        tool_events.append(
            {"tool": output_event, "started": elapsed(), "finished": elapsed()}
        )
        parts = [case.core_fact]
        if workflow["verified"]:
            parts.append(case.verification_receipt)
        if workflow["optional_obtained"]:
            parts.append(case.optional_fact)
        return " ".join(parts)

    client = AsyncOpenAI(
        base_url=host.rstrip("/") + "/v1",
        api_key="ollama-local",
        max_retries=0,
    )
    model = OpenAIChatCompletionsModel(model=model_name, openai_client=client)
    instructions = "Deliver the acceptance criteria and keep the final answer concise."
    if condition != "base":
        instructions += "\n\n" + TIME_RULES
    input_filter = None
    if condition in {"tracked", "controller"}:
        inject_time = make_call_model_input_filter(controller)

        def record_and_inject(data: Any):
            nonlocal first_warning
            state = controller.checkpoint()
            if state.get("action") not in (None, "continue") and first_warning is None:
                first_warning = elapsed()
            return inject_time(data)

        input_filter = record_and_inject
    run_config = RunConfig(
        tracing_disabled=True,
        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
        call_model_input_filter=input_filter,
    )
    result_output = ""
    error: str | None = None
    timed_out = False

    async def execute_workflow() -> str:
        for step in range(max_turns):
            if workflow["verified"] or workflow["required_blocked"]:
                return host_result()
            if not workflow["core_obtained"]:
                chosen_name = "get_core_fact"
                chosen_tool = get_core_fact
            else:
                available = {"verify_core_fact": verify_core_fact}
                if not workflow["optional_closed"]:
                    available["get_optional_fact"] = get_optional_fact
                if not workflow["checkpoint_reported"]:
                    available["report_checkpoint"] = report_checkpoint
                if len(available) == 1:
                    chosen_name, chosen_tool = next(iter(available.items()))
                else:
                    option_names = list(available)
                    random.Random(sample_seed + step).shuffle(option_names)
                    selection_agent = Agent(
                        name=f"time-benchmark-{condition}-decision-{step}",
                        instructions=instructions,
                        model=model,
                        model_settings=ModelSettings(
                            temperature=0,
                            extra_args={"seed": sample_seed},
                        ),
                    )
                    selection = await Runner.run(
                        selection_agent,
                        user_prompt(case, budget_seconds)
                        + "\n\nChoose the next action. Return exactly one available "
                        "tool name and no explanation. Verification is required; "
                        "optional enrichment and checkpointing are useful only when "
                        "they do not endanger completion. Available tool names: "
                        + ", ".join(option_names),
                        run_config=run_config,
                        hooks=TimeBudgetHooks(controller),
                        max_turns=1,
                    )
                    raw_selection = str(selection.final_output or "")
                    matches = {
                        name
                        for name in available
                        if re.search(rf"\b{re.escape(name)}\b", raw_selection)
                    }
                    if len(matches) != 1:
                        raise RuntimeError(
                            f"model returned ambiguous workflow action: {raw_selection!r}"
                        )
                    chosen_name = matches.pop()
                    chosen_tool = available[chosen_name]
                    decision_events.append(
                        {
                            "elapsed_seconds": elapsed(),
                            "available": option_names,
                            "selected": chosen_name,
                            "raw_output": raw_selection,
                        }
                    )
            step_prompt = (
                user_prompt(case, budget_seconds)
                + f"\n\nCall {chosen_name} now. Do not answer in text."
            )
            if chosen_name == "report_checkpoint":
                step_prompt += (
                    " Supply numeric low_seconds, likely_seconds, and high_seconds "
                    "for the remaining workflow, with low <= likely <= high."
                )
            before = (
                tuple(workflow.items()),
                len(tool_events),
                len(rejected_tool_events),
            )
            step_agent = Agent(
                name=f"time-benchmark-{condition}-step-{step}",
                instructions=instructions,
                model=model,
                model_settings=ModelSettings(
                    temperature=0,
                    tool_choice="required",
                    parallel_tool_calls=False,
                    extra_args={"seed": sample_seed},
                ),
                tools=[chosen_tool],
                tool_use_behavior="stop_on_first_tool",
                reset_tool_choice=False,
            )
            await Runner.run(
                step_agent,
                step_prompt,
                run_config=run_config,
                hooks=TimeBudgetHooks(controller),
                max_turns=1,
            )
            after = (
                tuple(workflow.items()),
                len(tool_events),
                len(rejected_tool_events),
            )
            if after == before:
                raise RuntimeError(
                    "model returned without invoking an enabled workflow tool"
                )
        raise RuntimeError("workflow exhausted max_turns before verification")

    try:
        if condition == "controller":
            result_output = await controller.run_until_hard_deadline(execute_workflow())
        else:
            result_output = await asyncio.wait_for(
                execute_workflow(), timeout=budget_seconds * 2
            )
    except (HardDeadlineReached, asyncio.TimeoutError) as exc:
        timed_out = True
        error = f"{type(exc).__name__}: {exc}"
        result_output = host_result()
        if condition == "controller" and first_warning is None:
            first_warning = min(elapsed(), budget_seconds)
    except Exception as exc:  # noqa: BLE001 - the benchmark must retain every run failure
        error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.close()

    actual_elapsed = elapsed()
    utility = score_output(case, result_output)
    if timed_out:
        outcome = "timed_out"
    elif utility >= 0.9:
        outcome = "complete"
    elif utility > 0:
        outcome = "partial"
    else:
        outcome = "failed"

    checkpoints: list[dict[str, float]] = []
    if not timed_out and not error:
        for checkpoint in raw_checkpoints:
            checkpoints.append(
                {
                    "estimate_low_seconds": checkpoint["estimate_low_seconds"],
                    "estimate_likely_seconds": checkpoint["estimate_likely_seconds"],
                    "estimate_high_seconds": checkpoint["estimate_high_seconds"],
                    "actual_remaining_seconds": max(
                        0.0,
                        actual_elapsed - checkpoint["observed_elapsed_seconds"],
                    ),
                }
            )

    return {
        "run_id": str(uuid.uuid4()),
        "task_id": case.task_id,
        "condition": condition,
        "model": model_name,
        "sample_seed": sample_seed,
        "tool_profile": (
            f"ollama-tools-v3:core={case_core_delay}:"
            f"optional={case_optional_delay}:verify={case_verify_delay}"
        ),
        "budget_seconds": budget_seconds,
        "actual_elapsed_seconds": actual_elapsed,
        "outcome": outcome,
        "deadline_met": actual_elapsed <= budget_seconds,
        "verified_utility": utility,
        "first_infeasible_warning_elapsed_seconds": first_warning,
        "checkpoints": checkpoints,
        "started_at": started_wall.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "reserve_seconds": reserve_seconds,
        "tool_events": tool_events,
        "decision_events": decision_events,
        "rejected_tool_events": rejected_tool_events,
        "final_output": result_output,
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run randomized matched time-control conditions on local Ollama."
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--budget-seconds", type=float, default=20.0)
    parser.add_argument("--reserve-seconds", type=float, default=5.0)
    parser.add_argument("--core-delay", type=float, default=0.4)
    parser.add_argument("--optional-delay", type=float, default=2.0)
    parser.add_argument("--verify-delay", type=float, default=0.4)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=CONDITIONS,
        default=list(CONDITIONS),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    if args.repetitions <= 0 or args.max_turns <= 0:
        raise ValueError("repetitions and max-turns must be positive")
    durations = (
        args.budget_seconds,
        args.reserve_seconds,
        args.core_delay,
        args.optional_delay,
        args.verify_delay,
    )
    if any(value < 0 for value in durations) or args.budget_seconds <= 0:
        raise ValueError(
            "budgets and delays must be non-negative; budget must be positive"
        )
    if args.reserve_seconds >= args.budget_seconds:
        raise ValueError("reserve-seconds must be smaller than budget-seconds")

    jobs = [
        (case, condition, repetition)
        for repetition in range(args.repetitions)
        for case in CASES
        for condition in args.conditions
    ]
    random.Random(args.seed).shuffle(jobs)
    plan = {
        "model": args.model,
        "conditions": args.conditions,
        "tasks": [case.task_id for case in CASES],
        "repetitions": args.repetitions,
        "runs": len(jobs),
        "budget_seconds": args.budget_seconds,
        "reserve_seconds": args.reserve_seconds,
        "randomization_seed": args.seed,
        "paired_sample_seeds": True,
        "latency_profiles": {
            case.task_id: dict(
                zip(
                    ("core_seconds", "optional_seconds", "verify_seconds"),
                    effective_delays(
                        case,
                        args.core_delay,
                        args.optional_delay,
                        args.verify_delay,
                    ),
                    strict=True,
                )
            )
            for case in CASES
        },
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if args.output is None:
        raise ValueError("--output is required unless --dry-run is used")
    if args.output.exists() and not args.overwrite:
        raise FileExistsError(f"output already exists: {args.output}")
    models = installed_models(args.host)
    if args.model not in models:
        raise ValueError(
            f"model {args.model!r} is not installed; available models: {models}. "
            "This evaluator never downloads models automatically."
        )

    await warm_model(args.host, args.model)
    records: list[dict[str, Any]] = []
    mode = "w" if args.overwrite else "x"
    with args.output.open(mode, encoding="utf-8") as handle:
        for index, (case, condition, repetition) in enumerate(jobs, start=1):
            record = await run_case(
                case=case,
                condition=condition,
                model_name=args.model,
                host=args.host,
                budget_seconds=args.budget_seconds,
                reserve_seconds=args.reserve_seconds,
                core_delay=args.core_delay,
                optional_delay=args.optional_delay,
                verify_delay=args.verify_delay,
                max_turns=args.max_turns,
                sample_seed=paired_sample_seed(args.seed, case, repetition),
            )
            record["repetition"] = repetition
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            records.append(record)
            print(
                f"[{index}/{len(jobs)}] {condition} {case.task_id}: "
                f"{record['outcome']} utility={record['verified_utility']:.1f} "
                f"elapsed={record['actual_elapsed_seconds']:.2f}s",
                file=sys.stderr,
            )
    print(
        json.dumps(
            evaluate_pilot_advancement(records, expected_records=len(jobs)),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except (FileExistsError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
