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
| Automatic tool gate | OpenAI function-tool guardrail; optional strict hook | recoverable rejection, reserve verification/handoff, and expiry tests | host designates action kinds; hosted tools without guardrails require strict host handling |
| Local caller deadline | async controller | prompt return and cooperative-cancellation tests | a cancellation-suppressing coroutine can continue in-process |
| Local process-group timeout cleanup | `deadline_run.py` | leader-exit, resistant-child, and signal-race tests | POSIX group only; bounded cleanup grace is additional time; non-POSIX terminates the direct process |
| Calibration summary | `calibration.py`, MCP tool | failure retention, ratio, coverage tests | no bundled real outcome corpus; no T4 claim |
| Timing observations | `TimingRecorder` | success/failure/timeout/cancellation and concurrent-scope tests | bounded memory only; host owns approved persistence |
| Distribution | Skill ZIP + wheel + sdist | relocated installation, manifest tampering and deterministic ZIP tests | checksum is integrity evidence, not a signature |
| Scheduling | none | none | must be supplied by host automation |

Historical 0.1.0 pilots (failed v1 and passing v2) remain under `evals/`.
Version 0.2.0 is a local delivery build. Use its dated release-validation receipts
for executed environments; configured CI jobs are not evidence that those jobs ran.
Local mechanism, package and synthetic-workflow tests do not establish calibration
on arbitrary tasks, other models or changed infrastructure. Broader real-task
validation remains required for behavioral generalization or T4 claims.
