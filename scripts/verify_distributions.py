"""Install built artifacts in fresh environments outside the checkout and test them."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


async def installed_smoke() -> dict:
    from mcp import Client, StdioServerParameters

    import agent_time_control as package
    from agent_time_control import TimeBudgetController, TimeContract
    from agent_time_control.adapters import openai_agents  # noqa: F401

    origin = Path(package.__file__).resolve()
    if not origin.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError(f"package imported outside the clean environment: {origin}")
    controller = TimeBudgetController(TimeContract.relative(60, reserve_seconds=10))

    async def work():
        return "verified"

    if await controller.run_until_hard_deadline(work()) != "verified":
        raise ValueError("installed controller smoke failed")
    executable = Path(sys.executable).parent / (
        "agent-time-mcp.exe" if os.name == "nt" else "agent-time-mcp"
    )
    params = StdioServerParameters(command=str(executable), args=[])
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        if names != {
            "time_now",
            "start_timebox",
            "check_deadline",
            "evaluate_checkpoint",
            "summarize_calibration",
            "forecast_remaining",
        }:
            raise ValueError("installed MCP discovery differs from the supported tools")
        state = await client.call_tool(
            "start_timebox", {"duration_seconds": 60, "reserve_seconds": 10}
        )
        if state.is_error or state.structured_content["remaining_seconds"] != 60:
            raise ValueError("installed MCP timebox failed")
        error = await client.call_tool("start_timebox", {"duration_seconds": -1})
        if not error.is_error:
            raise ValueError("installed MCP accepted an invalid timebox")
    return {
        "passed": True,
        "version": package.__version__,
        "import_origin": str(origin),
        "controller": "passed",
        "mcp_entry_point": "passed",
        "agents_adapter": "passed",
    }


def verify(distributions: Path) -> dict:
    artifacts = {}
    for kind, pattern in (
        ("wheel", "*.whl"),
        ("sdist", "*.tar.gz"),
        ("skill", "*.zip"),
    ):
        matches = list(distributions.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f"expected exactly one {kind}, found {len(matches)}")
        artifacts[kind] = matches[0].resolve()
    expected = {}
    for line in (distributions / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    for path in artifacts.values():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected.get(path.name):
            raise ValueError(f"distribution checksum mismatch: {path.name}")
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = {}
    with tempfile.TemporaryDirectory(prefix="agent-time-install-") as temp:
        base = Path(temp)

        def run(args: list[str], timeout: int = 180) -> str:
            result = subprocess.run(
                args,
                cwd=base,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            # Retain installation and smoke diagnostics in the caller's receipt log.
            print(result.stdout, end="", flush=True)
            print(result.stderr, end="", file=sys.stderr, flush=True)
            result.check_returncode()
            return result.stdout

        for kind in ("wheel", "sdist"):
            venv = base / kind
            run([sys.executable, "-m", "venv", str(venv)])
            python = str(
                venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            run([python, "-m", "pip", "install", f"{artifacts[kind]}[openai-agents]"])
            run([python, "-m", "pip", "check"])
            results[kind] = json.loads(
                run([python, "-I", str(Path(__file__).resolve()), "--installed"])
            )
        # The portable Skill and its installer must work without runtime packages.
        portable_env = base / "portable"
        run([sys.executable, "-m", "venv", "--without-pip", str(portable_env)])
        python = str(
            portable_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        with zipfile.ZipFile(artifacts["skill"]) as archive:
            archive.extractall(base / "zip")
        root = base / "zip/time-aware-execution"
        results["skill"] = json.loads(
            run([python, "-B", str(root / "scripts/verify_install.py")])
        )
        with tarfile.open(artifacts["sdist"]) as archive:
            # These are locally built artifacts, verified against SHA256SUMS above.
            archive.extractall(base / "source", filter="data")
        sources = list((base / "source").iterdir())
        if len(sources) != 1:
            raise ValueError("expected one source distribution root")
        project = base / "project"
        project.mkdir()
        rules = project / "AGENTS.md"
        original = b"# Existing team instructions\r\nPreserve this text.\r\n"
        rules.write_bytes(original)
        command = [
            python,
            "-B",
            str(sources[0] / "scripts/install_skill.py"),
            "--project",
            str(project),
        ]
        first = json.loads(run(command))
        before = rules.read_bytes()
        second = json.loads(run(command))
        if (
            first["status"] != "installed"
            or second["status"] != "unchanged"
            or not before.startswith(original)
            or rules.read_bytes() != before
        ):
            raise ValueError(
                "one-command installation did not preserve rules or repeat safely"
            )
        results["one_command"] = {"first": first, "repeat": second}
    return {"passed": True, "checks": results, "sha256": expected}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distributions", type=Path)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.installed:
        result = asyncio.run(installed_smoke())
    elif args.distributions:
        result = verify(args.distributions.resolve())
    else:
        parser.error("--distributions is required")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
