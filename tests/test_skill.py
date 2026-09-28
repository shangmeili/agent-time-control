from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SkillWorkflowTests(unittest.TestCase):
    def test_skill_metadata_and_bundled_references_resolve(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        metadata = dict(
            line.split(":", 1) for line in skill.split("---", 2)[1].strip().splitlines()
        )
        self.assertEqual(metadata["name"].strip(), "time-aware-execution")
        self.assertIn("deadline", metadata["description"])
        links = re.findall(r"\]\(([^)]+)\)", skill)
        scripts = re.findall(r"python3 (scripts/[a-z_]+\.py)", skill)
        self.assertTrue(links and scripts)
        for relative in links + scripts:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative.split("#", 1)[0]).is_file())
        interface = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$time-aware-execution", interface)

    def test_documented_cli_workflow_covers_all_six_gate_actions(self) -> None:
        def invoke(script: str, *arguments: str) -> dict:
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / script), *arguments],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        started = invoke(
            "deadline_clock.py",
            "--duration-minutes",
            "2",
            "--reserve-minutes",
            "1",
            "--now",
            "2026-09-28T00:00:00Z",
        )
        self.assertEqual(started["remaining_seconds"], 120)
        cases = (
            ("00:00:00", (10, 20, 30), "continue"),
            ("00:00:00", (10, 20, 90), "continue_core_only"),
            ("00:00:00", (10, 90, 100), "replan_and_reduce_scope"),
            ("00:00:00", (90, 100, 110), "reduce_scope_or_handoff"),
            ("00:01:30", (0, 0, 0), "verify_and_handoff"),
            ("00:02:00", (0, 0, 0), "stop"),
        )
        for observed, estimates, action in cases:
            with self.subTest(action=action):
                result = invoke(
                    "budget_gate.py",
                    "--deadline",
                    started["deadline"],
                    "--started-at",
                    started["started_at"],
                    "--reserve-minutes",
                    "1",
                    "--now",
                    f"2026-09-28T{observed}Z",
                    "--estimate-low-seconds",
                    str(estimates[0]),
                    "--estimate-likely-seconds",
                    str(estimates[1]),
                    "--estimate-high-seconds",
                    str(estimates[2]),
                )
                self.assertEqual(result["action"], action)
                self.assertEqual(result["deadline"], started["deadline"])
