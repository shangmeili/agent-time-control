# Changelog

## Unreleased — release validation repair

- Declare the backend required by non-isolated release builds.
- Include distribution building and clean wheel/source/Skill installation in the
  release receipt, with cross-platform CI evidence retained as artifacts.
- Add the existing staged Skill installer with instruction preservation, explicit
  replacement backups, rollback and repeat-install verification.

## 0.2.0 — local delivery build, not published

- Preserve the 0.1.0 APIs and add empirical remaining-step forecasting, a stdio
  forecast tool and an opt-in bounded `TimingRecorder` with unique observation IDs.
- Correct DST arithmetic, reserve verification/handoff, expired-task cancellation,
  process-group cleanup after leader exit, and rejected-call release checks.
- Reject invalid durations, numeric overflow, duplicate observation IDs and invalid
  time contracts. Keep an expired controller expired through clock rollback.
- Clean up on external SIGTERM and provide conventional subprocess exit codes.
- Add a portable checksummed Skill bundle, installation self-test, source/wheel
  distributions, explicit compatibility notes and a fail-closed release validator.
- Retain original pilots, failed experiments and later held-out comparisons without
  relabeling them. Empirical duration ranges remain uncalibrated scenarios.

## 0.1.0

Original external-clock, deterministic budget gate, MCP service, coroutine deadline
wrapper, Agents SDK adapter and local-model pilot. Retained public evidence is in
`evals/results/`.
