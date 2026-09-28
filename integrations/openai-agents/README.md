# OpenAI Agents SDK adapter

Use three controls together:

1. `make_call_model_input_filter(controller)` refreshes and injects machine-derived
   time state before every model call.
2. `make_tool_input_guardrail(...)` rejects a function-tool call with a
   model-visible result, allowing the agent to degrade and hand off instead of
   crashing the run.
3. `controller.run_until_hard_deadline(Runner.run(...))` enforces the caller's
   local deadline and requests task cancellation. Use process isolation when the
   work itself must be contained.

The adapter targets `openai-agents>=0.22,<1` and is covered by tests using the
actual SDK types. It does not make remote cancellation claims.

For deterministic, recoverable pre-action degradation, attach a guardrail when
creating each function tool:

```python
budget_guardrail = make_tool_input_guardrail(
    controller,
    estimated_seconds=30,
    optional=True,
)


@function_tool(tool_input_guardrails=[budget_guardrail])
def slow_search(): ...
```

By default, the guardrail rejects work whose adjusted duration cannot fit before
the reserve. Optional tools additionally require a current remaining-work forecast
and are rejected whenever the current gate action is more restrictive than
`continue`. `TimeBudgetHooks(controller)` remains an observer by default. Use
`strict=True` only for fail-closed tool classes that cannot carry a recoverable
input guardrail; an exception from a strict hook terminates the run.

Designate required verification or handoff tools in host configuration so they can
use the reserve without admitting new work:

```python
verify_guardrail = make_tool_input_guardrail(
    controller,
    estimated_seconds=5,
    action_kind="verification",
)

@function_tool(tool_input_guardrails=[verify_guardrail])
def verify_result(): ...
```

Use `action_kind="handoff"` for a final handoff tool. Both kinds must fit before
the hard deadline and are rejected after expiry. Optional work cannot claim either
kind. For strict hooks, the equivalent host setting is
`tool_action_kinds={"verify_result": "verification", "submit_result": "handoff"}`.
Action kinds are host policy; do not expose them as model-selectable tool arguments.
