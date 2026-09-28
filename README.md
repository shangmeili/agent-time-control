# Agent Time Control

Agent Time Control is a host-side time-control layer for AI agents. It gives an
agent a real wall clock, immutable timeboxes, deterministic budget gates,
read-only calibration summaries, automatic model/tool checkpoints, and local
deadline enforcement. The optional Skill supplies planning practice; it is not
the enforcement mechanism.

Status: `0.2.0` local delivery build, not yet published. Licensed under Apache-2.0.
Install the portable Skill ZIP or Python wheel using [INSTALL.md](INSTALL.md).
The source includes fail-closed release validation and reproducible Skill packaging.
Prior 0.1.0 pilot results are retained; they are not certification of this build.
CI is configured for Linux, macOS and Windows; actual validation receipts identify
which environments were executed.

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
- A local MCP 2.x server with six structured tools:
  `time_now`, `start_timebox`, `check_deadline`, `evaluate_checkpoint`,
  `summarize_calibration`, and `forecast_remaining`.
- A framework-neutral `TimeBudgetController`.
- Opt-in, bounded `TimingRecorder` measurement with unique observation IDs and
  retained failure/cancellation outcomes, without prompts, outputs or implicit storage.
- Remaining-work forecasts from caller-supplied comparable step timings, with
  explicit missing/censored evidence and no implicit history access; see
  [remaining-work forecasting](references/forecasting.md).
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
Required verification and handoff tools may use the reserve when the host sets
`action_kind="verification"` or `action_kind="handoff"` on their guardrail; all
other work keeps the execution-window limit. See the adapter integration guide.

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
evaluator live in [`evals/`](evals/). The v1 harness failure and the independently
seeded passing v2 result are both retained; see
[`evals/PILOT_V2_RESULTS.md`](evals/PILOT_V2_RESULTS.md).

## Build and validate a delivery

```bash
python -m pip install -e '.[test,openai-agents,release]'
python scripts/validate_release.py --output eval-results/release-validation
python scripts/build_release.py --python-dist --output dist/agent-time-control-0.2.0
```

Use new output directories; previous receipts and artifacts are never overwritten.
The ZIP contains the runtime scripts/source, reference files, install self-test and
per-file SHA-256 manifest. The wheel supplies the Python/MCP integration. Source
packages include tests and build tooling. Private `eval-results/`, Git metadata and
build caches are excluded. See [CHANGELOG.md](CHANGELOG.md) and [SECURITY.md](SECURITY.md).

## Assurance

The repository distinguishes:

- T1: external clock grounding;
- T2: refreshed budget tracking and deterministic gates;
- T3: host-enforced cancellation for the operations actually wrapped;
- T4: measured calibration on comparable retained outcomes.

The current implementation initiates local subprocess timeout cleanup at the
execution limit. POSIX process-group cleanup has an additional bounded termination
grace period; descendants outside that group and non-POSIX process trees are not
contained. Wrapped local async runs enforce the caller's deadline and request
task cancellation, but cannot contain a coroutine that suppresses cancellation.
It does not yet claim T4, general remote cancellation, or cross-model behavioral
improvement.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
