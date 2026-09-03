# Behavioral evaluation protocol

Status: preregistered pilot design, 2026-09-02. No scored model runs had been
performed when this protocol was written.

## Question and claims

The pilot asks whether adding progressively stronger time-control mechanisms to
the same tool-using agent changes what it delivers under a wall-clock budget.
It compares:

1. `base`: task instructions only;
2. `rules`: time-priority rules but no live time state;
3. `tracked`: rules plus refreshed external-clock state;
4. `controller`: tracking plus deterministic tool gates and a local caller
   deadline with cooperative cancellation.

The pilot can validate integration and expose behavioral regressions. With three
synthetic task templates and three repetitions, it cannot establish population
generalization, statistical significance, or T4 calibration.

## Fixed design

- One approved, already-installed tool-capable model is used for all conditions.
- A matched block is one task template and repetition number. All four conditions
  in a block share the same model, prompt, tools, budget, acceptance test, and
  best-effort sampling seed.
- Block execution order is randomized once from the recorded randomization seed.
- Tool execution is serialized. The model is warmed before measurement.
- The host runs one bounded model decision per workflow stage and accepts completion
  only from observed tool state. This avoids depending on provider-specific
  multi-turn `tool_choice` enforcement while preserving the model's choice among
  checkpointing, optional work, and verification.
- Three latency profiles are fixed before observing results: slow optional work,
  slow required-core work, and slow verification work.
- Failures, timeouts, tool errors, and partial outputs are retained. Runs are not
  silently retried or excluded.

The default pilot is 36 runs: 3 templates x 4 conditions x 3 repetitions. These
templates share one acceptance structure, so they are latency-shape probes rather
than independent samples of real-world tasks.

## Outcomes

Primary descriptive outcomes:

- verified utility delivered in the final output (`0.6` required fact, `0.3`
  verification receipt, `0.1` optional fact);
- deadline violation rate;
- completion rate, where completion requires both the required fact and receipt.

Diagnostic outcomes:

- elapsed time;
- checkpoint interval coverage, width, and likely-estimate absolute error;
- early-warning lead time on failed or timed-out runs;
- tool order, rejected actions, errors, and timeouts.

Controller guardrail rejections are recorded separately from tool-body execution,
so the pilot can distinguish an active intervention from an unused mechanism.

Utility is computed from exact hidden acceptance tokens, not model self-rating.
The evaluator must report `matched_design: true` and `paired_design: true`; it
rejects unequal task-condition counts and flags incomplete or seed-mismatched
blocks.

## Pilot advancement gate

The implementation is eligible for a `0.1.0` release candidate only if:

1. all mechanism, adapter, protocol, package, and Skill validation gates pass;
2. every planned record is retained and the evaluator confirms the matched,
   paired design, at least 36 records, three task templates, nine matched blocks,
   and all four conditions;
3. every controller run returns no later than `0.5` seconds after its local
   deadline; the tolerance covers event-loop scheduling and record finalization,
   not continued useful work;
4. there is no controller-specific harness failure or execution of a rejected
   function tool;
5. controller mean verified utility is no more than `0.1` below base and
   controller completion loses no more than one matched block relative to base.

The last tolerance equals the smallest utility increment in this synthetic
acceptance test and is a pilot regression bound, not a confidence interval. A
failed gate triggers diagnosis or redesign; it must not be hidden by removing
runs, changing thresholds, or selecting a favorable seed.

Passing permits only the claim that the published mechanisms work locally and did
not show a material regression in this synthetic pilot. It does not permit the
claim that agents in general became time-aware or that estimates are calibrated.

## Confirmatory work after the pilot

A behavioral-improvement or T4 claim requires a new, frozen evaluation set drawn
from target agent workloads, more task structures and latency regimes, and a
prospective sample-size analysis. The pilot may estimate plausible effect sizes
and paired variance, but observed pilot significance must not be used as proof.

For paired binary outcomes, use a paired analysis such as McNemar's test. For
continuous verified utility, preselect an appropriate paired permutation or
bootstrap analysis and report effect intervals. Because power depends on effect
size, variance, test, and sample size, simulate power over a range of practically
meaningful effects before collecting the confirmatory runs.

## Methodological basis

- Dror et al., [The Hitchhiker's Guide to Testing Statistical Significance in
  Natural Language Processing](https://aclanthology.org/P18-1128/), reviews test
  selection and paired randomization/bootstrap methods.
- Card et al., [With Little Power Comes Great
  Responsibility](https://aclanthology.org/2020.emnlp-main.745/), explains why
  small evaluations can miss, exaggerate, or reverse effects and recommends
  prospective power analysis across plausible parameter values.
