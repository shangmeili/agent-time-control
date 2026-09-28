"""Run fail-closed local release checks and retain machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_hashes(root: Path) -> dict[str, str]:
    paths = {
        root / name
        for name in (
            "pyproject.toml",
            "SKILL.md",
            "MANIFEST.in",
            "INSTALL.md",
            "CHANGELOG.md",
            "SECURITY.md",
            "README.md",
            "CONFORMANCE.md",
            "TIME_AWARENESS_STANDARD.md",
            "agents/openai.yaml",
            ".github/workflows/test.yml",
        )
    }
    for directory, pattern in (
        ("src/agent_time_control", "*.py"),
        ("scripts", "*.py"),
        ("tests", "*.py"),
        ("evals", "*.py"),
        ("examples", "*.py"),
        ("schemas", "*.json"),
        ("references", "*.md"),
    ):
        paths.update((root / directory).rglob(pattern))
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New receipt directory"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(
            "output already exists; preserve previous receipts and choose a new path"
        )
    output.mkdir(parents=True)
    before = source_hashes(ROOT)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    checks = {}
    dependencies = {}
    sys.path.insert(0, str(ROOT / "src"))
    try:
        for module, distribution in (
            ("mcp", "mcp"),
            ("agents", "openai-agents"),
            ("jsonschema", "jsonschema"),
            ("yaml", "PyYAML"),
            ("coverage", "coverage"),
            ("ruff", "ruff"),
            ("build", "build"),
        ):
            importlib.import_module(module)
            dependencies[distribution] = importlib.metadata.version(distribution)
        importlib.import_module("agent_time_control.mcp_server")
        importlib.import_module("agent_time_control.adapters.openai_agents")
        checks["required_dependencies"] = {"passed": True}
    except ImportError as exc:
        checks["required_dependencies"] = {"passed": False, "error": str(exc)}

    def run(name: str, arguments: list[str], timeout: int = 120) -> str:
        try:
            result = subprocess.run(
                [sys.executable, *arguments],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            text = result.stdout + result.stderr
            checks[name] = {
                "passed": result.returncode == 0,
                "exit_code": result.returncode,
                "log": name + ".log",
            }
        except subprocess.TimeoutExpired as exc:
            text = str(exc)
            checks[name] = {"passed": False, "error": "timeout", "log": name + ".log"}
        (output / (name + ".log")).write_text(text, encoding="utf-8")
        return text

    run("dependency_consistency", ["-m", "pip", "check"])
    run(
        "lint",
        [
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "src",
            "scripts",
            "evals",
            "tests",
            "examples",
        ],
    )
    run(
        "format",
        [
            "-m",
            "ruff",
            "format",
            "--check",
            "--no-cache",
            "src",
            "scripts",
            "evals",
            "tests",
            "examples",
        ],
    )
    if checks["required_dependencies"]["passed"]:
        text = run(
            "unit_tests",
            [
                "-m",
                "coverage",
                "run",
                "--data-file",
                str(output / ".coverage"),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ],
        )
        count = re.search(r"Ran (\d+) tests?", text)
        skips = re.findall(r"skipped '([^']+)'", text)
        unexpected = [s for s in skips if not (os.name != "posix" and "POSIX" in s)]
        checks["unit_tests"].update(
            {
                "tests": int(count.group(1)) if count else 0,
                "skipped": len(skips),
                "unexpected_skips": unexpected,
            }
        )
        checks["unit_tests"]["passed"] &= bool(
            count and int(count.group(1)) > 0 and not unexpected
        )
        run(
            "coverage",
            [
                "-m",
                "coverage",
                "report",
                "--data-file",
                str(output / ".coverage"),
                "--fail-under",
                "85",
            ],
        )
        run(
            "coverage_json",
            [
                "-m",
                "coverage",
                "json",
                "--data-file",
                str(output / ".coverage"),
                "-o",
                str(output / "coverage.json"),
            ],
        )
        run("installation", ["-B", "scripts/verify_install.py", "--mcp"])
    after = source_hashes(ROOT)
    checks["source_unchanged_during_validation"] = {"passed": before == after}
    report = {
        "version": "0.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "dependencies": dependencies,
        "checks": checks,
        "source_sha256": after,
        "passed": all(c["passed"] for c in checks.values()),
    }
    coverage_path = output / "coverage.json"
    if coverage_path.exists():
        report["coverage"] = json.loads(coverage_path.read_text())["totals"]
    (output / "validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": checks,
                "report": str(output / "validation.json"),
                "coverage": report.get("coverage"),
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
