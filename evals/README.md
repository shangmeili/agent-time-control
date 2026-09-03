# Behavioral evaluation protocol

The frozen pilot hypotheses, advancement gate, limitations, and confirmatory
analysis requirements are in [`EXPERIMENT_PROTOCOL.md`](EXPERIMENT_PROTOCOL.md).
The retained first pilot and its failed gate are summarized in
[`PILOT_V1_RESULTS.md`](PILOT_V1_RESULTS.md); its records must not be silently
reclassified as successful runs.
The amended, independently seeded pilot passed its frozen gate; see
[`PILOT_V2_RESULTS.md`](PILOT_V2_RESULTS.md) and the raw retained evidence in
[`results/`](results/).

Mechanism tests do not prove that a language model delivers more useful work by a
deadline. A release-level behavioral evaluation should compare matched conditions:

1. base agent with no time mechanism;
2. always-on rules only;
3. rules plus external clock and refreshed budget state;
4. rules, state, deterministic gate, automatic hooks, and a local caller deadline.

For each task and condition, keep the same model snapshot, tool profile, task
version, budget, environment, and acceptance test. Randomize condition order and
repeat runs when stochasticity is material. Retain partial, failed, and timed-out
runs.

Each JSONL record supplied to `scripts/evaluate_runs.py` contains:

```json
{
  "run_id": "unique-id",
  "task_id": "matched-task-id",
  "condition": "controller",
  "model": "exact-model-version",
  "tool_profile": "fixed-toolset-v1",
  "budget_seconds": 600,
  "actual_elapsed_seconds": 590,
  "outcome": "complete",
  "deadline_met": true,
  "verified_utility": 0.9,
  "first_infeasible_warning_elapsed_seconds": null,
  "checkpoints": [
    {
      "estimate_low_seconds": 200,
      "estimate_likely_seconds": 280,
      "estimate_high_seconds": 400,
      "actual_remaining_seconds": 300
    }
  ]
}
```

`verified_utility` must come from a task-specific acceptance test normalized to
`0..1`, not from model self-rating. The evaluator reports completion, deadline
violations, verified utility, interval coverage and width, forecast error, and
early-warning lead time. It also rejects duplicate runs and flags unmatched task,
budget, or tool designs.

The output is descriptive. Do not claim causality or statistical significance
without enough repeated randomized matched runs and an analysis appropriate to the
task distribution.

## Local Ollama runner

`run_ollama.py` exercises the actual OpenAI Agents adapter through Ollama's local
OpenAI-compatible endpoint. It compares `base`, `rules`, `tracked`, and
`controller` with the same model, tools, acceptance tests, budgets, and randomized
condition order. It never downloads a model automatically.

Preview the matched design without running a model:

```bash
python evals/run_ollama.py --model qwen2.5:0.5b --dry-run
```

After separately installing an approved model:

```bash
python evals/run_ollama.py \
  --model qwen2.5:0.5b \
  --repetitions 3 \
  --budget-seconds 20 \
  --reserve-seconds 5 \
  --output eval-results.jsonl
```

The runner warms the model before measurement, serializes tool execution, flushes
each run record immediately, retains failures and timeouts, and uses deterministic
acceptance tokens rather than model self-ratings. Each task/repetition block shares
one sampling seed across conditions while condition order is randomized. Seeded
sampling is best-effort rather than proof of bit-for-bit reproducibility. The
evaluator rejects missing task-condition cells, unequal repetition counts, and
model/tool/budget mismatches. A small local model is useful for integration evidence
but cannot establish generalization to frontier or hosted models.
