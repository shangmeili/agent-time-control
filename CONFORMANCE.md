# Implementation conformance

This file maps the draft standard to observable implementation evidence. It is a
scope statement, not a certification.

| Capability | Implementation | Current evidence | Boundary |
|---|---|---|---|
| Clock grounding | `core.py`, MCP `time_now` | timezone and fractional-boundary tests | host system clock, not an independent time authority |
| Immutable relative timebox | `create_timebox`, MCP `start_timebox` | original-start and reserve validation tests | caller must retain returned contract |
| Budget tracking | `TimeBudgetController`, MCP snapshots | phase transition and stdio tests | MCP-only hosts can still omit tool calls |
| Deterministic degradation | `decide` | all six actions and multiplier transition tests | forecast supplied by agent or caller remains uncertain |
| Automatic model checkpoints | OpenAI Agents input filter | adapter test refreshes execute to reserve | only implemented for OpenAI Agents SDK |
| Automatic tool gate | OpenAI function-tool guardrail; optional strict hook | recoverable rejection and fail-closed tests | hosted tools without guardrails require strict host handling |
| Local caller deadline | async controller | prompt return and cooperative-cancellation tests | a cancellation-suppressing coroutine can continue in-process |
| Contained local hard deadline | `deadline_run.py` | process termination tests | child processes require platform-specific process-group handling |
| Calibration summary | `calibration.py`, MCP tool | failure retention, ratio, coverage tests | no bundled real outcome corpus; no T4 claim |
| Scheduling | none | none | must be supplied by host automation |

Release still requires the preregistered local-model behavioral pilot, a fresh
package verification after any resulting changes, public repository publication,
and public CI. Broader real-task evaluation remains required for behavioral
generalization or T4 claims.
