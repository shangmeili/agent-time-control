# Qwen2.5 0.5B pilot v2 result

Date: 2026-09-03  
Frozen implementation commit: `bd65a16`  
Randomization and paired-sampling base seed: `20261003`  
Model: `qwen2.5:0.5b`, Ollama digest
`a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`

## Gate result

Pilot v2 passed every preregistered advancement check. All 36 planned records
were retained with unique run IDs, nine runs per condition, three task templates,
nine matched blocks, and matching paired seeds. There were no deadline violations
or recorded harness errors.

| Condition | Complete | Partial or failed | Deadline violations | Mean utility |
|---|---:|---:|---:|---:|
| base | 9/9 | 0/9 | 0/9 | 0.9111 |
| rules | 9/9 | 0/9 | 0/9 | 0.9444 |
| tracked | 9/9 | 0/9 | 0/9 | 0.9111 |
| controller | 9/9 | 0/9 | 0/9 | 0.9000 |

The controller-to-base utility difference was `-0.0111`, inside the frozen
regression tolerance of `-0.1`. Controller completion matched base at 9/9, and
the slowest controller return was 5.56 seconds under a 20-second deadline.

In one controller run the model selected optional work before reporting a current
remaining-work forecast. The host rejected that action, retained the required
workflow, and the run subsequently verified and completed with utility `0.9`.

## Estimate evidence and claim boundary

The controller condition emitted three valid checkpoint ranges, all `20/20/20`
seconds. Interval coverage was `0.0`, with median absolute likely-estimate error
of 18.49 seconds. This pilot therefore does not establish calibrated estimates,
T4 assurance, statistical significance, or generalization to real workloads or
other models.

Passing supports only the release claim frozen in the protocol: the published
mechanisms work locally and did not show a material regression in this synthetic
pilot. Model estimates remain advisory; the external clock, deterministic gate,
reserve policy, and cancellation boundary provide control.

## Retained evidence

- [`qwen2.5-0.5b-pilot-v2.jsonl`](results/qwen2.5-0.5b-pilot-v2.jsonl), SHA-256
  `7b5f872bdce82ae962dfefbaf9cce3e1694e66160bbd677ba6010a1e55699efe`
- [`qwen2.5-0.5b-report-v2.json`](results/qwen2.5-0.5b-report-v2.json), SHA-256
  `5be1d300aa82df91e4defb43bbcff01d87b3f4c3a49ca9d7bbbd332767d926a9`

The failed v1 records and report are also retained in `evals/results/`; v1 and v2
were not merged.
