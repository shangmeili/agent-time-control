# Qwen2.5 0.5B pilot v1 result

Date: 2026-09-03  
Frozen implementation commit: `dcee7ae`  
Model: `qwen2.5:0.5b`, Ollama digest
`a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`

## Gate result

Pilot v1 did not pass the preregistered advancement gate. All 36 planned records
were retained, run IDs were unique, the four conditions were balanced at nine
runs each, and both matched and paired design checks passed. The failing check was
`no_controller_harness_errors`.

| Condition | Complete | Partial | Failed | Deadline violations | Mean utility |
|---|---:|---:|---:|---:|---:|
| base | 7/9 | 0/9 | 2/9 | 0/9 | 0.7111 |
| rules | 7/9 | 0/9 | 2/9 | 0/9 | 0.7333 |
| tracked | 8/9 | 0/9 | 1/9 | 0/9 | 0.8333 |
| controller | 7/9 | 1/9 | 1/9 | 0/9 | 0.8000 |

These are descriptive synthetic results, not evidence of statistical
significance or generalization.

## Failure analysis

Six runs ended with `model returned without invoking an enabled workflow tool`:
two base, two rules, one tracked, and one controller. In each case, the model had
already selected an exact action name in a text decision step. The failure arose
in the redundant next request that asked the same 0.5B model to encode that action
again as an OpenAI-compatible tool call. Direct compatibility probes confirmed
that this model can call a single explicitly named tool, but does not reliably
honor `tool_choice=required` across action shapes.

This is an evaluation transport incompatibility, not evidence that the selected
time-control condition failed. It also cannot be silently excluded or retried,
because the v1 protocol retained all harness failures.

One separate controller run produced a valid partial handoff with utility `0.7`.
The model chose optional work, then reported a `10/20/30` second remaining-work
range. At 14.1 seconds elapsed, the deterministic gate rejected verification as
unable to fit before the five-second reserve. This is a genuine adverse trajectory:
the mechanism preserved a handoff but did not prevent optional work early enough.

Checkpoint interval coverage was `0` in every condition that emitted checkpoints.
The small model generally returned `10/20/30` or `20/20/20`, far above the
observed remaining time. This supports the design decision that model estimates
must remain advisory and empirically calibrated.

## V2 amendment before new data

Pilot v2 removes only the redundant action-to-tool-call transport step:

- the model still chooses among checkpointing, optional work, and verification;
- the model still supplies its own remaining-work range when it chooses a
  checkpoint;
- the host executes the selected synthetic action directly;
- the same deterministic controller gates the action before execution;
- all conditions share the same executor and frozen acceptance test;
- v1 records remain retained and are not merged with v2.

V2 uses the frozen randomization and paired-sampling base seed `20261003` and must
independently pass the same advancement thresholds. This amendment is intended to
measure time decisions rather than a provider-specific serialization failure.
