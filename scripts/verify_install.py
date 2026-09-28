"""Offline installation smoke test. --mcp also exercises the installed MCP dependency."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path, *, check_mcp: bool = False) -> dict:
    if sys.version_info < (3, 10):  # noqa: UP036 - standalone diagnostics also run before installation.
        raise ValueError(
            "Python 3.10 or newer is required; select an explicit interpreter"
        )
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n"):
        raise ValueError("SKILL.md frontmatter is missing")
    front = skill.split("---", 2)[1]
    name = re.search(r"^name:\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$", front, re.MULTILINE)
    description = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
    if (
        name is None
        or name.group(1) != "time-aware-execution"
        or len(name.group(1)) > 64
    ):
        raise ValueError("invalid skill name")
    if (root / "BUNDLE-MANIFEST.json").exists() and name.group(1) != root.name:
        raise ValueError("installed skill name must match its directory")
    if description is None or not 1 <= len(description.group(1)) <= 1024:
        raise ValueError("skill description must have 1..1024 characters")
    for link in re.findall(r"\]\(([^)]+)\)", skill):
        if "://" not in link:
            target = (root / link.split("#", 1)[0]).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError(f"missing or unsafe skill reference: {link}")
    manifest_file = root / "BUNDLE-MANIFEST.json"
    manifest_checked = False
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        for relative, expected in manifest["files"].items():
            target = (root / relative).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError(f"missing or unsafe manifest entry: {relative}")
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError(f"checksum mismatch: {relative}")
        manifest_checked = True

    def invoke(script: str, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-B", str(root / "scripts" / script), *args],
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{script}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    state = invoke(
        "deadline_clock.py",
        "--duration-minutes",
        "2",
        "--reserve-minutes",
        "1",
        "--now",
        "2026-09-28T00:00:00Z",
    )
    if state["remaining_seconds"] != 120 or state["execution_remaining_seconds"] != 60:
        raise ValueError("clock smoke did not produce the expected budget")
    gate = invoke(
        "budget_gate.py",
        "--deadline",
        state["deadline"],
        "--now",
        "2026-09-28T00:02:01Z",
        "--estimate-low-seconds",
        "0",
        "--estimate-likely-seconds",
        "0",
        "--estimate-high-seconds",
        "0",
    )
    if gate["action"] != "stop":
        raise ValueError("expired gate did not stop")
    example = subprocess.run(
        [sys.executable, "-B", str(root / "examples/local_workflow.py")],
        cwd=root.parent,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if example.returncode or not json.loads(example.stdout).get("verified"):
        raise RuntimeError(f"local workflow failed: {example.stderr}")
    result = {
        "passed": True,
        "skill": name.group(1),
        "python": sys.version.split()[0],
        "manifest_verified": manifest_checked,
        "clock": "passed",
        "gate": "passed",
        "measured_workflow": "passed",
        "mcp": "not_requested",
    }
    if check_mcp:
        result["mcp"] = asyncio.run(verify_mcp(root))
    return result


async def verify_mcp(root: Path) -> str:
    from mcp import Client, StdioServerParameters

    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_time_control.mcp_server"], env=env
    )
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
            raise ValueError("MCP discovery differs from the supported tool set")
        state = await client.call_tool(
            "start_timebox", {"duration_seconds": 60, "reserve_seconds": 10}
        )
        if state.is_error or state.structured_content["remaining_seconds"] != 60:
            raise ValueError("MCP timebox smoke failed")
        error = await client.call_tool("start_timebox", {"duration_seconds": -1})
        if not error.is_error:
            raise ValueError("MCP accepted an invalid timebox")
    return "passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(ROOT, check_mcp=args.mcp)
    except (
        OSError,
        ValueError,
        RuntimeError,
        ImportError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
