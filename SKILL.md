---
name: time-aware-execution
description: Plan, estimate, and execute deadline-constrained work using a real clock, calibrated ranges, checkpoints, and explicit scope tradeoffs. Use when a user gives a deadline or timebox, asks for an ETA or duration estimate, or needs reliable progress against elapsed wall-clock time; this skill does not itself create scheduled wakeups.
---

# Time Aware Execution

Treat time as external state, not intuition. The objective is a useful result before the deadline with an honest account of uncertainty—not a confident-looking timestamp.

This Skill is the on-demand adapter for the repository's always-on [Time Awareness Standard](TIME_AWARENESS_STANDARD.md). If the host already injects that baseline, use this Skill for its detailed workflow and bundled tools rather than duplicating policy text.

## Establish the time contract

Before making a time claim, identify:

- the deliverable and its acceptance test;
- whether the user supplied an absolute deadline, a relative timebox, or only requested an estimate;
- the clock source, timezone, and start time;
- whether the limit concerns wall-clock time, active compute time, human effort, or response latency;
- external waits such as approvals, queues, downloads, builds, or another person.

Use a system clock or time tool. If neither is available, say that live deadline tracking is unavailable and provide a conditional plan instead of promising on-time completion. Interpret a relative timebox from a recorded start timestamp, not from conversational intuition. Require or infer timezone only from reliable context; state any inference that could change the deadline.

For deterministic deadline arithmetic, run:

```bash
python3 scripts/deadline_clock.py \
  (--deadline <ISO-8601-with-timezone> | --duration-minutes <minutes>) \
  [--started-at <ISO-8601-with-timezone>] [--reserve-minutes <minutes>]
```

Read `phase`, `remaining_seconds`, and `execution_remaining_seconds` from the JSON. The script observes time; it does not schedule, wake, interrupt, or persist an agent.

For a new relative timebox, omit `--started-at` on the first call and record the returned `started_at`; supply that same timestamp on later checks so the budget is not accidentally reset.

## Estimate from evidence

Do the cheapest inspection that can materially change the estimate before forecasting. Prefer evidence in this order:

1. measured outcomes from comparable prior executions in the same environment;
2. a reference class of similar tasks;
3. decomposition into observable stages after inspecting the relevant system;
4. an explicitly labeled rough guess when none of the above exists.

Report a range, not a falsely precise point estimate. Include:

- the likely elapsed-time range;
- confidence (`low`, `medium`, or `high`) and why;
- assumptions and the largest overrun drivers;
- the next observation that will allow recalibration.

Keep human-equivalent effort, agent runtime, and elapsed delivery time separate. Do not convert benchmark “task horizon” values into an ETA. Do not treat parallel work as additive elapsed time. Do not include user or approval wait inside active execution time without labeling it.

When an authorized JSONL history exists, inspect a comparable task class with:

```bash
python3 scripts/calibration_report.py --input <records.jsonl> \
  [--task-class <comparable-class>]
```

Use the report's sample size, completion rate, completed-run P50/P80, and error multiplier as evidence—not as an automatic answer. Treat small or heterogeneous samples as low confidence. Keep partial and failed runs visible; a fast successful subset alone creates survivorship bias. The report is read-only and never creates a history file.

If the user requests one number, give a planning figure only after giving the range, and identify whether it is the median-like working estimate or a conservative commitment bound. Never imply a statistical confidence level when there is no calibrated historical distribution.

## Plan backward from the deadline

Reserve enough time for integration, verification, and reporting in proportion to failure cost. Then select the smallest scope that satisfies the acceptance test within the remaining execution window.

Create checkpoints at meaningful boundaries, not arbitrary narration intervals. At each checkpoint, forecast remaining work as `low <= likely <= high`; this is a progressive estimate from the current state, not a repeat of the initial estimate. Every checkpoint must answer:

- What has been completed and verified?
- How much wall-clock time remains according to the clock?
- What is the updated remaining-work interval, and what evidence changed it?
- Is completion still feasible before the execution window closes?
- What scope should be retained, deferred, or dropped?

Pass the interval through the deterministic gate:

```bash
python3 scripts/budget_gate.py \
  --deadline <ISO-8601-with-timezone> --reserve-minutes <minutes> \
  --estimate-low-seconds <seconds> --estimate-likely-seconds <seconds> \
  --estimate-high-seconds <seconds> \
  [--calibration-multiplier <observed-ratio>]
```

Follow `stop` and `verify_and_handoff`. Treat the other actions as strong control signals, then apply task-specific safety and acceptance constraints. Never reduce scope silently. Because model-produced intervals can be optimistic, do not override an adverse gate result merely by generating a new unsupported interval.

Prefer an early vertical slice or decisive experiment that reduces uncertainty. Do not spend most of the budget planning an untested full solution.

## Execute with feedback

Re-read the clock:

- after initial inspection;
- after an uncertain, external, or tool-heavy stage;
- before expanding scope;
- when a checkpoint is reached;
- before entering final verification.

Update the range from observed stage durations. State a changed forecast promptly; never preserve an obsolete ETA for appearance.

For an already-authorized local subprocess that must not overrun, use the host-enforced wrapper:

```bash
python3 scripts/deadline_run.py \
  (--deadline <ISO-8601-with-timezone> | --timeout-seconds <seconds>) \
  [--reserve-seconds <seconds>] -- <command> [args...]
```

Exit code `124` means the command was not started or was terminated at its limit. This wrapper does not authorize the command, undo its side effects, constrain remote work after cancellation, or preempt model inference and tools controlled by another host.

When time tightens, degrade in this order unless the user specified otherwise:

1. remove optional polish and unrelated improvements;
2. reduce breadth while preserving a working, testable core;
3. preserve verification of the highest-risk behavior;
4. produce a recoverable partial result with exact remaining work;
5. stop at the hard deadline or host-enforced timeout.

Do not silently skip safety checks, destructive-action review, data integrity, or required authorization to meet a deadline. A deadline does not broaden permissions.

## Report honestly

Lead with the delivered result and verification status. Then report:

- start, finish, deadline, and timezone when relevant;
- actual elapsed time and any external wait;
- estimate versus actual, if an estimate was given;
- deferred scope and why;
- whether the deadline was met, missed, or cannot be verified.

Record observed durations when a durable project-local mechanism already exists and the user has authorized writing to it. Never invent historical timings or create persistent memory implicitly.

## Route scheduling separately

If the request requires waking later, recurrence, notification, or monitoring while no turn is active, use the host product's scheduler or automation facility. A prompt, loop, or long sleep is not a scheduler. For a production control plane or hard timeout, read [references/host-integration.md](references/host-integration.md).

For the empirical basis, limitations, and source links behind this protocol, read [references/evidence.md](references/evidence.md) when evaluating or revising the skill rather than during routine execution.
