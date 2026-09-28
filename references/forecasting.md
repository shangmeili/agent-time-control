# Remaining-work forecasting

Give an estimator an explicit remaining plan, completed work, comparable tool
measurements and model-call latency. A deadline describes available time; it must
not be reused as the estimate or used to truncate a longer estimate. Forecasts
start after the estimation call returns. Report that call's cost separately.

## Observed duration path

`forecast_remaining_work` provides a standard-library path independent of model
arithmetic. Supply only past observations and the exact serial operations still
needed, including model decisions, queues, verification and handoff. Remove
completed steps. Repeat a step name when multiple invocations remain. Do not use
serial addition for overlapping parallel work.

Each observation has `reference_class`, `operation`, `elapsed_seconds`, and
`outcome` (`complete`, `partial`, `failed`, or `timed_out`). The caller defines a
reference-class key for the same model snapshot, tool/workload version and host.
Different keys are never pooled. Measure elapsed durations with a monotonic clock;
a configured delay or a model guess is not an observation.

The function requires at least five completed measurements per required operation
and no censored/failed measurements for those operations. It sums each operation's
observed minimum, median and maximum. This is an empirical scenario range, not a
confidence interval. Insufficient or censored evidence returns
`status="insufficient_evidence"` and `remaining_work_interval=null`, with the
missing operations and sample counts. Do not drop failed samples to obtain a
finite answer or interpret missing work as zero.

```python
from agent_time_control import TimeBudgetController, TimeContract

controller = TimeBudgetController(TimeContract.relative(60, reserve_seconds=10))
state = controller.update_forecast_from_history(
    remaining_steps=["model_decision", "verify", "handoff"],
    observations=authorized_past_observations,
    reference_class="model-snapshot/tool-profile/workload/host",
)
```

Refresh with the new remaining plan after progress or scope changes. Insufficient
history invalidates the previous plan's forecast. The resulting checkpoint exposes
`forecast_evidence`, sample counts and `empirical_range_not_calibrated` to the model.
Ordinary gate and cancellation rules remain active; a forecast never authorizes
work. A multiplier, when configured, remains visible in the checkpoint.

The MCP tool `forecast_remaining` accepts the same three arguments. A standalone
CLI reads one JSON object with those fields:

```bash
python3 scripts/forecast_remaining.py --input forecast-request.json
```

Neither path reads a history implicitly or writes one. Hosts must collect and
supply authorized measurements. When no suitable observations exist, supply the
model with the observed progress and explicit assumptions and label its estimate
as unvalidated. The model prompt must not claim calibrated precision.

## Collect measurements in host code

```python
from agent_time_control import TimingRecorder

recorder = TimingRecorder("model-snapshot/tool-profile/workload/host")
with recorder.measure("model_decision"):
    result = await authorized_model_call()
with recorder.measure("verify"):
    verify_result(result)
observations = recorder.snapshot()
```

Each completed scope records a unique `observation_id`, monotonic duration and
outcome. Failed, timed-out and cancelled scopes remain visible; payloads and error
messages are not recorded. `snapshot()` returns copies. A full recorder rejects
new measurement scopes before work rather than dropping records. Storage and
retention remain explicit host responsibilities. Duplicate supplied IDs are
rejected; legacy observations without IDs remain readable. Requests are bounded
to 10000 observations and 1000 remaining serial steps. Match the host's transport
byte/rate limits too. After progress, resupply the plan; use
`controller.invalidate_forecast()` when the old estimate is no longer relevant.

The offline example `examples/local_workflow.py` demonstrates measurement through
forecasting, gating and verification without a model API.

## Independent verification

`evals/evaluate_forecasts.py` measures real local model calls and synthetic
verification latency. It first gathers timing observations, then freezes them
before a new held-out set. Original-prompt, grounded-prompt and empirical-step
forecasts are recorded before the same target execution. The target is one model
decision, verification and host handoff; it excludes the cost of requesting the
forecasts, which is reported separately. All failures and raw model outputs are
retained. Coverage, error and interval width are reported together.

```bash
python evals/evaluate_forecasts.py --model qwen2.5:0.5b \
  --calibration 12 --holdout 12 --seed 20260929 \
  --output eval-results/forecast-evaluation.jsonl
```

Use an already running, authorized local model server. The script never downloads
models or overwrites outputs. This evaluates a defined synthetic workflow, not
accuracy for unknown tasks, other models, changing hosts or long-running work.
Calibration and test outcomes must stay separate, including when choosing
thresholds. An empirical interval's coverage must be re-evaluated after workload
or environment changes.
