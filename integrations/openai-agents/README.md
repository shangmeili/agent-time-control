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

The guardrail rejects any action whose adjusted duration cannot fit before the
reserve, and rejects optional tools whenever the current gate action is more
restrictive than `continue`. `TimeBudgetHooks(controller)` remains an observer by
default. Use `strict=True` only for fail-closed tool classes that cannot carry a
recoverable input guardrail; an exception from a strict hook terminates the run.
