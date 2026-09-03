# Time Awareness Standard for Agents

Status: Draft 0.2

This standard defines a host-independent baseline for agents that plan or act under wall-clock constraints. It separates language-level reasoning from clock access, scheduling, runtime enforcement, and empirical calibration.

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** describe normative requirements for claiming the corresponding assurance level.

## 1. Scope

Apply the standard when a task includes any of the following:

- an absolute deadline or relative timebox;
- a request for an ETA, duration estimate, scheduled wakeup, recurrence, or monitoring;
- an operation whose usefulness or safety changes materially with elapsed time;
- a long-running workflow where tool latency, queues, approvals, or retries can consume the delivery window.

Ordinary short tasks without a meaningful time constraint do not need checkpoint overhead.

## 2. Separate capabilities

An implementation MUST NOT collapse these into a single claim of “time awareness”:

1. **Temporal reasoning:** interpreting dates, ordering, duration, and recurrence.
2. **Clock grounding:** reading current wall-clock time and timezone from the environment.
3. **Budget tracking:** measuring elapsed and remaining wall-clock time during execution.
4. **Scheduling:** starting or waking a run at a future time or external event.
5. **Deadline enforcement:** preventing work from continuing past a hard boundary.
6. **Duration forecasting:** estimating the remaining work with uncertainty.
7. **Calibration:** demonstrating that forecast intervals and warnings match observed outcomes.

## 3. Assurance levels

Use the highest label whose requirements are actually satisfied:

| Level | Claim | Required evidence |
|---|---|---|
| T0 | time-language capable | Can interpret time expressions; no live clock claim |
| T1 | clock-grounded | External time source, timezone, recorded start and deadline |
| T2 | budget-tracked | T1 plus refreshed elapsed/remaining state at checkpoints |
| T3 | deadline-enforced | T2 plus host cancellation or scheduler-enforced stop |
| T4 | empirically calibrated | T3 plus comparable outcome history and measured forecast performance |

An Agent Skill or prompt alone can normally reach at most T2. T3 requires a host mechanism. T4 requires outcome data and evaluation.

## 4. Time contract

Before issuing an ETA or accepting a time constraint, the agent MUST establish:

- the deliverable and observable acceptance test;
- whether the constraint is a deadline, a timebox, active compute budget, human-effort estimate, or response-latency target;
- start time, deadline, timezone, and authoritative clock source;
- verification or handoff reserve;
- external waits and whether they count toward elapsed delivery time;
- scope priority: required core, optional breadth, and polish.

Relative timeboxes MUST use a recorded start timestamp. Re-reading “now” and restarting the duration is prohibited. A timezone-free absolute deadline MUST NOT be silently treated as an exact instant unless reliable user or environment context supplies the timezone; any material inference must be disclosed.

If no live clock is available, the agent MUST describe its plan as conditional and MUST NOT claim live deadline tracking.

## 5. Forecast contract

Forecasting MUST distinguish:

- human-equivalent effort;
- active agent or compute time;
- external wait time;
- total wall-clock delivery time.

After the cheapest scope-changing inspection, the agent SHOULD use evidence in this order:

1. comparable observed runs in the same environment;
2. a broader relevant reference class;
3. inspected stage decomposition;
4. an explicitly labeled rough scenario estimate.

A forecast SHOULD contain `low`, `likely`, and `high` remaining-work values, assumptions, confidence, and the next recalibration event. Without historical coverage evidence, this is a scenario range—not a calibrated prediction interval.

## 6. Progressive checkpoints

At each meaningful checkpoint, the implementation MUST refresh clock state and SHOULD emit a machine-readable record conforming to `schemas/time-checkpoint.schema.json`.

The checkpoint must include:

- authoritative observation time and remaining execution window;
- completed and verified evidence;
- a new remaining-work interval from the current trajectory state;
- feasibility and the control action;
- retained and deferred scope.

Checkpoint estimates MUST be newly conditioned on observed progress. Copying the initial estimate forward does not satisfy progressive estimation.

Useful checkpoint triggers include completion of initial inspection, return from an uncertain or high-latency tool, discovery of a blocker, proposed scope expansion, transition into verification, and a material forecast change.

## 7. Deterministic control gate

The model's self-estimate is advisory. A deterministic controller SHOULD compare the adjusted remaining-work interval with the remaining execution window:

- no current remaining-work forecast and optional work is proposed → reject the optional work and request a checkpoint;
- hard deadline reached → `stop`;
- execution window exhausted but reserve remains → `verify_and_handoff`;
- adjusted low exceeds the window → `reduce_scope_or_handoff`;
- adjusted likely exceeds the window → `replan_and_reduce_scope`;
- adjusted high exceeds the window → `continue_core_only`;
- full interval fits → `continue`.

The controller MAY apply a multiplier supported by comparable historical errors. It MUST expose that multiplier and MUST NOT present an unsupported safety factor as statistically calibrated.

An adverse gate result MUST NOT be overridden by asking the same model for a more convenient estimate without new evidence.

## 8. Degradation order

Unless the user defines another priority, reduce work in this order:

1. optional polish and unrelated improvements;
2. breadth that does not affect the working core;
3. lower-value exploration or redundant attempts;
4. scope beyond a recoverable, testable partial result.

Preserve required authorization, destructive-action checks, data integrity, and verification of the highest-risk behavior. A deadline never expands permission or justifies concealing reduced scope.

As budget depletes, shift from exploration toward integration, acceptance-test evidence, and handoff. Prefer marginal progress toward the acceptance test over absolute self-ratings such as “the solution is 80% good.”

## 9. Scheduling and enforcement

Future wakeups, recurrence, and monitoring MUST use a scheduler or event trigger outside the inactive model. Long sleeps and remembered prompts are not schedulers.

Hard deadlines MUST be enforced by the host for model calls, tools, subprocesses, queues, and remote jobs in scope. Cancelling a local client does not prove that a remote action stopped. Side effects that cannot be rolled back require the same authorization they would require without a deadline.

## 10. Calibration and evaluation

An implementation claiming T4 MUST retain permitted observations from completed, partial, failed, and timed-out runs. It SHOULD evaluate:

- interval coverage and width at each checkpoint;
- median and tail actual/estimate ratios;
- deadline violation rate;
- normalized early-warning lead time for infeasible runs;
- verified utility delivered by the deadline;
- completion rate under matched budgets;
- external-wait and tool-latency sensitivity.

Evaluation SHOULD compare at least:

1. no time mechanism;
2. always-on rules only;
3. rules plus clock/budget tracking;
4. rules, tracking, deterministic gate, and deadline enforcement.

Use the same task, model, tool access, and budget across conditions. Randomize condition order where possible and retain failures. Final success alone cannot identify whether forecasting, warning, control, or execution improved.

## 11. Reporting

The final report MUST state the delivered and verified result first. When time was material, it must then identify:

- start, finish, deadline, and timezone;
- actual wall-clock time and labeled external waits;
- forecast changes and final estimate-versus-actual;
- deadline status;
- deferred scope and exact remaining work;
- assurance level T0–T4 and the missing evidence for any higher level.

## 12. Privacy and persistence

Historical timings can reveal project scope, work habits, infrastructure performance, or operational events. Implementations MUST follow the host's data policy and user authorization. They MUST NOT silently create durable histories, upload traces, or claim learning from observations that were not retained.
