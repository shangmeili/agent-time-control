# Host integration for hard time guarantees

The skill improves decisions inside an active agent turn. Reliable wakeups, hard deadlines, and recovery require a host control plane.

## Required components

1. **Wall clock:** Inject `now`, start time, deadline, and timezone on every agent iteration. Use a monotonic clock for measuring durations and a timezone-aware wall clock for calendar deadlines.
2. **Scheduler:** Persist future or recurring triggers outside the model. The scheduler starts a new run; the model does not keep itself alive by remembering a date.
3. **Deadline enforcement:** Bound each tool call or subprocess by the smaller of its step limit and the remaining execution budget. The host, not the model, must terminate work at a hard deadline.
4. **Durable state:** Save the goal, acceptance test, completed artifacts, verification evidence, remaining work, last forecast, and next checkpoint. Resume from state rather than reconstructed conversational memory.
5. **Telemetry:** Record timestamps for queueing, model inference, tool execution, external waits, retries, verification, and total elapsed delivery time.
6. **Policy:** Define what may be dropped as time runs out and what must never be skipped, including authorization, destructive-action checks, and high-risk verification.

## Minimal control loop

```text
load durable state
while wall_clock < hard_deadline:
    remaining = hard_deadline - wall_clock
    if remaining <= verification_reserve:
        mode = VERIFY_AND_HAND_OFF
    inject clock snapshot, remaining budget, prior estimate, mode, and state
    run one bounded agent step
    validate and persist the step result
    require a new low/likely/high remaining-work interval
    compare the interval with remaining execution time in a deterministic gate
    update duration observations, feasibility, and forecast
terminate active work at hard_deadline
emit the best verified result plus exact remaining work
```

Use process-level cancellation for hard timeouts. A model instruction such as “stop after 30 minutes” is advisory and cannot enforce cancellation while a tool call is blocked.

The bundled `scripts/deadline_run.py` enforces this boundary for one local subprocess. A production host must provide equivalent cancellation for model calls, remote tools, queues, and child jobs; cancelling the local client does not prove that remote work stopped.

## Implemented adapters

The repository exposes the pure controller in `src/agent_time_control/controller.py`.
Its MCP server provides portable clock and gate calls, but MCP availability alone
does not force an agent to call them.

For OpenAI Agents SDK, `src/agent_time_control/adapters/openai_agents.py` adds:

- a `call_model_input_filter` that refreshes time before every model call;
- a recoverable function-tool input guardrail that returns a budget rejection to
  the model, plus an optional strict `RunHooks` boundary for tool classes without
  guardrail support;
- a host wrapper that requests cancellation and promptly returns control at the
  hard deadline.

Other hosts should implement the same three interception points: pre-model context,
pre-action gate, and outer-run cancellation. Python coroutine cancellation is
cooperative; use a killable worker process or verified provider cancellation API
when continued execution would be unsafe. Rules or Skills remain useful for
scope semantics, but they are not substitutes for those interceptors.

## Estimation data

For each recurring task class, retain only measurements the user or project policy permits:

```text
task_class
environment_or_repository
scope_features
started_at / completed_at
active_agent_seconds
external_wait_seconds
verification_seconds
outcome: complete | partial | failed
scope_change
estimate_low_seconds / estimate_likely_seconds / estimate_high_seconds
estimate_checkpoint
```

Forecast from comparable successful and failed runs. Keep failed and timed-out runs in the reference class; excluding them creates survivorship bias.

## Reliability boundary

“Deadline-aware” means the agent observes and responds to time. “Deadline-guaranteed” additionally requires host enforcement, bounded dependencies, enough capacity, and a deliverable whose acceptance criteria can still be met. Do not use the latter term without those controls.
