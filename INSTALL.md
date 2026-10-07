# Install and operate Agent Time Control 0.2.0

## One-command installation from a checkout

```bash
python3 scripts/install_skill.py
```

Use an available Python 3.10+ interpreter. The installer builds and self-tests a
portable Skill, installs it in `~/.agents/skills/time-aware-execution`, and merges
one managed block into `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`). If an
`AGENTS.override.md` exists, it uses that effective instruction file instead.
Existing text is preserved byte-for-byte outside the managed block. Repeating an
unchanged installation makes no changes. Different or locally edited installations
require `--replace`; replaced content and existing rules are backed up outside the
Skill discovery tree. A rule-write failure rolls back the Skill installation.

Use `--project /absolute/project/path` to install project-locally for a team or an
isolated acceptance test. This command installs no Python packages, MCP server or
model. Run from a checkout or extracted source distribution. Start a new agent session
to verify host discovery and actual use; file installation alone does not prove
agent activation.

## Choose the execution surface

The portable Skill contains its scripts and Python source. Its clock, gate,
forecast, measurement example and installation check need only Python 3.10+.
The optional MCP server requires the Python package dependencies. Agents SDK
integration additionally requires the `openai-agents` extra. A Skill folder does
not install a server, grant tool access or start a background service.

## Portable Skill

Unpack into a new staging directory, then verify it before moving it into a
host-discovered location. Use an explicit Python 3.10+ interpreter; macOS's
`/usr/bin/python3` may be older.

```bash
python3.11 -m zipfile -e time-aware-execution-0.2.0.zip ./skill-staging
python3.11 ./skill-staging/time-aware-execution/scripts/verify_install.py
```

The verifier checks manifest hashes, references, the clock/gate and an offline
measured workflow while running outside the Skill directory. Expected JSON:
`passed: true`, `manifest_verified: true`, `measured_workflow: passed`.

For local Codex discovery, place the entire `time-aware-execution` folder in
`$HOME/.agents/skills/` or a repository's `.agents/skills/`. Do not install a second
copy with the same name. Back up any existing version outside the discovery tree
before replacement. Invoke `$time-aware-execution` in Codex CLI/IDE. Selection and
activation by a particular GUI still require an acceptance check in that host.

## MCP and optional Agents SDK

Create a dedicated environment and install the delivered wheel. Dependencies may
require network access during installation; the stdio server itself does not.

```bash
python3.11 -m venv .venv-time-control
.venv-time-control/bin/python -m pip install './agent_time_control-0.2.0-py3-none-any.whl[openai-agents]'
.venv-time-control/bin/python ./skill-staging/time-aware-execution/scripts/verify_install.py --mcp
```

On Windows the environment executable is `.venv-time-control/Scripts/python.exe`.
Configure a stdio MCP server using that environment's absolute Python path:

```json
{
  "mcpServers": {
    "agent-time-control": {
      "command": "/absolute/path/to/.venv-time-control/bin/python",
      "args": ["-m", "agent_time_control.mcp_server"]
    }
  }
}
```

The six tools are `time_now`, `start_timebox`, `check_deadline`,
`evaluate_checkpoint`, `summarize_calibration` and `forecast_remaining`.
MCP-only use observes budgets; wrap execution with the controller for enforcement.
For Agents SDK configuration see `integrations/openai-agents/README.md` in the
source distribution. `action_kind` is host configuration, not a model argument.

## Measure before forecasting

Use `TimingRecorder(reference_class).measure(operation)` around authorized work,
including awaited model/tool calls, verification and handoff. It holds timing
records in memory, retaining failures and cancellations without recording prompts,
arguments, outputs or exception messages. Export `snapshot()` only through a
host-approved persistence mechanism. No files or network writes occur implicitly.

Reference classes must distinguish model snapshot, tool/workload version and host.
Refresh the remaining plan after progress. Call `invalidate_forecast()` when the
plan is no longer valid. Missing measurements yield `insufficient_evidence`; they
are never zero-cost work. The serial estimator is not a parallel-work estimator.
A full recorder raises `BufferError` before work; export and create a new recorder,
keeping incomplete outcomes in the retained reference class.

```bash
python3.11 examples/local_workflow.py
```

This example is fully offline and does not claim to test a language model.

## Error and deadline contract

CLI validation failures return 2. `deadline_run.py` returns 124 on timeout, 125 on
cleanup failure, 126/127 on executable failure/not found, and 128+signal for signal
termination. It never uses a shell to interpret command arguments. The reservation
argument on that command applies to absolute deadlines only.

Default controller time is pinned to an elapsed monotonic clock and never moves
backward after an observed forward clock jump. An already expired window stays
expired. Long awaits refresh the calendar clock at most every 0.25 seconds while
the event loop runs; blocking Python code can prevent cooperative cancellation.
Custom wall clocks are deterministic test inputs; inject a matching
`monotonic_clock` when elapsed tracking is also required.

POSIX cleanup targets the isolated process group and handles normal leader exit,
timeout, Ctrl-C and SIGTERM. Grace (default 2 seconds) and reaping (bounded at 5
seconds) are cleanup time beyond the work budget. Children that escape the group,
remote side effects, SIGKILL of the wrapper and an unresponsive kernel require a
supervisor with stronger containment. Windows currently terminates the direct
process only. These are operating characteristics, not timer precision guarantees.

## Verification, upgrade and rollback

From the source distribution:

```bash
python -m pip install -e '.[test,openai-agents,release]'
python scripts/validate_release.py --output eval-results/release-validation
```

Validation requires a new output directory and leaves existing results intact.
It builds the ZIP, wheel and sdist into that directory's `distributions/`, then
installs the wheel and sdist with the Agents SDK extra into separate fresh virtual
environments outside the checkout. It checks dependency consistency, package import
location, the controller and the installed MCP console entry point. It also verifies
the extracted ZIP and runs the source distribution's one-command Skill installer
twice in a temporary project, preserving pre-existing rules. These checks require
network access to install dependencies; they do not install into user Skill paths
or prove discovery in a live agent session.

The release extra explicitly installs setuptools because `build_release.py` uses
`build --no-isolation`. The backend installed temporarily by an editable install's
build isolation is not available to this later command. A missing backend or a
failed distribution/installation check makes the validation receipt fail.

To build only, run `python scripts/build_release.py --python-dist --output dist/new`.
Review the validation report, checksum manifest and compatibility section before
rollout. Test the new Skill in staging with the target host and workload, then
replace the old folder/environment during a planned change. Keep the previous
folder and virtual environment for rollback; restore them and reselect the old
configuration if acceptance checks fail. Do not reuse measurement classes across
incompatible workloads. The repository's historical pilots are retained as
historical evidence, not a fresh production certification.

Specification and installation references:
https://agentskills.io/specification
https://developers.openai.com/codex/skills
