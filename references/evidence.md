# Evidence and design rationale

This note records why the skill uses an external clock, reference classes, ranges, feedback checkpoints, and a separate scheduler. It is not required for routine invocation.

## 1. Temporal reasoning is not a clock

Wang and Zhao's [TRAM temporal-reasoning benchmark](https://arxiv.org/abs/2310.00835) covers ordering, arithmetic, frequency, and duration. Its best evaluated model remained significantly behind human performance. This supports checking temporal arithmetic deterministically, but the benchmark does not test whether an inactive agent can wake itself later.

Design implication: obtain current time from the environment and calculate deadlines with code rather than relying on generated intuition.

## 2. Agents need environmental ground truth and stopping conditions

Anthropic's [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) describes agents as models operating in a tool-and-environment feedback loop and recommends explicit stopping conditions. OpenAI's [Scheduled tasks documentation](https://help.openai.com/en/articles/10291617-what-is-agent-mode) exposes future and recurring execution through a separate scheduling feature.

Design implication: keep active-turn deadline behavior in the skill, but route wakeups and recurrence to host automation.

## 3. Model duration estimates are useful but poorly calibrated

Anthropic's [Estimating AI productivity gains](https://www.anthropic.com/research/estimating-productivity-gains) compared Claude Sonnet 4.5 with 1,000 real Jira tasks. Claude's estimates had Spearman correlation `0.44` with actual time versus `0.50` for developers' own estimates. The model compressed the range and tended to overestimate short tasks and underestimate long ones.

Design implication: use estimates for relative planning, prefer observed reference classes, report ranges and assumptions, and update the forecast from actual stage durations.

## 4. The outside view counters planning bias

The planning-fallacy literature defines a recurring tendency to underestimate completion time despite experience with overruns. Buehler, Griffin, and Peetz's [review](https://doi.org/10.1016/S0065-2601(10)43001-4) describes the inside-outside model; Flyvbjerg's [reference-class forecasting work](https://doi.org/10.1080/09654310701747936) bases forecasts on actual outcomes from comparable completed projects. A study of [backward planning](https://sjdm.org/~baron/journal/16/16101/jdm16101.html) found longer and, in one study, less biased predictions, apparently by drawing attention to obstacles and interruptions.

Design implication: inspect the real task, compare it with similar outcomes, plan backward from acceptance, and name overrun drivers.

## 5. Human-equivalent task length is not agent runtime

METR's [Task-Completion Time Horizons](https://metr.org/time-horizons/) defines task length using skilled-human completion time and explicitly says that a time horizon measures task difficulty, not how long the AI runs. Runtime varies substantially with the model, task, inference provider, and agent setup.

Design implication: never turn a benchmark horizon or “hours of human work” into a delivery ETA without local runtime evidence.

## 6. Wall-clock time must replace token proxies

[Timely Machine](https://arxiv.org/abs/2601.16486) argues that tool latency decouples generation length from elapsed time. Its Timely-Eval experiments report that existing models fail to adapt reasoning reliably to time budgets across different tool-latency regimes. The paper is a 2026 preprint under review, so its quantitative results should be treated as provisional.

Design implication: observe wall-clock time after tool interactions and test the skill under different latency shapes; token count and tool-call count are useful secondary budgets, not substitutes for time.

## 7. Remaining-budget estimation is a separate capability

[BAGEN](https://arxiv.org/abs/2606.00198) formalizes budget awareness as progressive interval estimation: at each step, predict lower and upper remaining-budget bounds and warn when completion is unlikely. Across five frontier agents and four environments, it reports only `r=0.35` correlation between task strength and budget awareness, systematic optimism, late failure recognition, and interval coverage no higher than 47% even after task-specific training. Early stopping saved 28–64% of tokens on failed trajectories. This is also a 2026 preprint.

Design implication: require a fresh remaining-work interval at checkpoints, evaluate feasibility separately from task quality, keep an external deterministic gate, and measure early-warning lead time rather than only final deadline success.

## 8. Continuous budget signals can improve agent behavior

[Budget-Aware Tool-Use Enables Effective Agent Scaling](https://arxiv.org/abs/2511.17006) adds a lightweight Budget Tracker after each tool response and reports consistent accuracy gains under matched budgets for web-search agents; one comparison used 40.4% fewer search calls, 21.4% fewer browse calls, and 31.3% lower unified cost at similar accuracy. [BAVT](https://arxiv.org/abs/2603.12634) further argues for step-level relative-progress signals and a shift from broad exploration toward exploitation as budget depletes, rather than relying on absolute self-evaluation. Both results are task- and framework-specific preprints.

Design implication: expose remaining budget continuously, preserve a persistent plan, prefer marginal acceptance-test progress over self-rated absolute quality, and narrow exploration as the execution window closes.

## 9. Small evaluations need paired tests and prospective power analysis

Dror et al.'s [significance-testing guide](https://aclanthology.org/P18-1128/)
reviews paired randomization and bootstrap methods for NLP comparisons. Card et
al.'s [power-analysis study](https://aclanthology.org/2020.emnlp-main.745/) shows
that statistical power depends on the test, effect size, variance, and sample size;
underpowered results can miss effects or exaggerate their magnitude and even sign.
It recommends examining power over plausible parameter ranges before evaluation
rather than relying on post-hoc power computed from one observed result.

Design implication: preserve task-level pairing, verify equal condition counts,
preregister the pilot gate, retain all outcomes, and treat the bundled small-model
experiment as integration evidence. Use a prospective, simulation-based sample-size
analysis before any confirmatory behavioral claim.

## Limits of the evidence

- The strongest model-estimation result above is from software tasks and should not be generalized quantitatively to every domain.
- Reference-class quality depends on genuinely comparable cases and unbiased retention of failures.
- A range produced without historical data is a judgmental scenario range, not a calibrated prediction interval.
- A skill can shape decisions but cannot enforce wakeups, preempt blocked tools, or guarantee capacity; those are host responsibilities.
- Current budget-awareness studies are recent preprints and often evaluate search, games, or software tasks; they support mechanisms and evaluation criteria, not universal effect sizes.
