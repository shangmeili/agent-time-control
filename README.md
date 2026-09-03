# Agent Time Control

Agent Time Control is a host-side time-control layer for AI agents. It gives an
agent a real wall clock, immutable timeboxes, deterministic budget gates,
read-only calibration summaries, automatic model/tool checkpoints, and local
deadline enforcement. The optional Skill supplies planning practice; it is not
the enforcement mechanism.

Status: pre-release `0.1.0`. The mechanisms and interfaces are tested and the
repository is licensed under Apache-2.0; the preregistered behavioral pilot and
public repository checks are still release gates.

## Why this exists

Language models can reason about dates without continuously observing elapsed
time. Their duration estimates are also imperfect: Anthropic found that one
frontier model compressed the range of software-task estimates, overestimating
short work and underestimating long work. Recent budget-awareness research also
reports late failure recognition and weak interval coverage. See
[`references/evidence.md`](references/evidence.md) for sources and limitations.

This project therefore separates five jobs:

1. a normative time contract;
2. an external clock and deterministic decision service;
3. host middleware that checks time automatically;
4. hard cancellation outside the model;
5. empirical calibration from comparable outcomes.

## What is implemented

- Pure Python clock, timebox, forecast, and control-gate primitives.
- A local MCP 2.x server with five structured tools:
  `time_now`, `start_timebox`, `check_deadline`, `evaluate_checkpoint`, and
  `summarize_calibration`.
- A framework-neutral `TimeBudgetController`.
- An OpenAI Agents SDK adapter that refreshes state before every model call and
  blocks new local tool work after the execution window closes.
- Prompt caller-deadline return for local coroutines and contained subprocess
  deadline enforcement.
- JSON Schema, CLI helpers, an optional Agent Skill, and an always-on rule snippet.

It does not schedule future runs, authorize actions, guarantee cancellation of a
remote side effect, or make an unvalidated estimate statistically calibrated.

## Install and run the MCP server

Python 3.10 or newer is required.

```bash
python -m pip install -e .
agent-time-mcp
```

Generic local MCP configuration:

```json
{
  "mcpServers": {
    "agent-time-control": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "agent_time_control.mcp_server"]
    }
  }
}
```

The server uses stdio and does not read files, execute commands, schedule jobs, or
persist timing history. The caller supplies calibration records explicitly.

## Host-enforced OpenAI Agents run

Install the adapter extra:

```bash
python -m pip install -e '.[openai-agents]'
```

```python
from agents import Agent, RunConfig, Runner
from agent_time_control.adapters.openai_agents import (
    TimeBudgetHooks,
    make_call_model_input_filter,
    make_tool_input_guardrail,
)
from agent_time_control.controller import TimeBudgetController, TimeContract

contract = TimeContract.relative(duration_seconds=900, reserve_seconds=120)
controller = TimeBudgetController(contract)
controller.update_forecast(300, 480, 720)

agent = Agent(name="worker", instructions="Deliver the required core first.")
config = RunConfig(call_model_input_filter=make_call_model_input_filter(controller))

result = await controller.run_until_hard_deadline(
    Runner.run(
        agent,
        "Complete the task",
        run_config=config,
        hooks=TimeBudgetHooks(controller),
    )
)
```

The input filter automatically injects a fresh snapshot before each model call.
Attach `make_tool_input_guardrail` to function tools that need recoverable budget
rejection. `TimeBudgetHooks` observes lifecycle state by default; strict mode is a
fail-closed option for tool classes without guardrails. The outer wrapper requests
cancellation and returns control at the hard deadline. Python coroutine
cancellation remains cooperative: a cancellation-suppressing task, remote provider,
or tool may continue unless it is process-isolated or its API confirms cancellation.

## Standalone tools

The scripts run with the Python standard library and do not require installation:

```bash
python3 scripts/deadline_clock.py --duration-minutes 30 --reserve-minutes 5

python3 scripts/budget_gate.py \
  --deadline 2026-09-02T18:00:00+08:00 \
  --reserve-minutes 5 \
  --estimate-low-seconds 300 \
  --estimate-likely-seconds 600 \
  --estimate-high-seconds 900

python3 scripts/deadline_run.py --timeout-seconds 30 -- command arg
```

See [`TIME_AWARENESS_STANDARD.md`](TIME_AWARENESS_STANDARD.md) for the behavior
contract and [`references/host-integration.md`](references/host-integration.md)
for production boundaries.

## Test

```bash
python -m pip install -e '.[test,openai-agents]'
python -m unittest discover -s tests -v
```

The suite exercises pure logic, fractional deadline boundaries, every control
action, subprocess cancellation, JSON Schema, in-process MCP, real stdio MCP, the
OpenAI input filter and tool hook, prompt asynchronous deadline return, and the
boundary of cooperative cancellation.
The preregistered pilot, matched-run protocol, Ollama runner, and descriptive
evaluator live in [`evals/`](evals/).

## Assurance

The repository distinguishes:

- T1: external clock grounding;
- T2: refreshed budget tracking and deterministic gates;
- T3: host-enforced cancellation for the operations actually wrapped;
- T4: measured calibration on comparable retained outcomes.

The current implementation demonstrates contained T3 behavior for local
subprocesses. Wrapped local async runs enforce the caller's deadline and request
task cancellation, but cannot contain a coroutine that suppresses cancellation.
It does not yet claim T4, general remote cancellation, or cross-model behavioral
improvement.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
