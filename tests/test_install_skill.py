from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install_skill import install, merge_instructions


class SkillInstallerTests(unittest.TestCase):
    def test_reinstall_after_normal_use_preserves_generated_bytecode(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            first = install(ROOT, project=project)
            target = Path(first["skill_dir"])
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            subprocess.run(
                [
                    sys.executable,
                    str(target / "scripts/deadline_clock.py"),
                    "--duration-minutes",
                    "1",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                check=True,
                timeout=10,
            )
            caches = list(target.rglob("*.pyc"))
            self.assertTrue(caches)
            self.assertEqual(install(ROOT, project=project)["status"], "unchanged")
            self.assertTrue(all(path.exists() for path in caches))

    def test_user_paths_respect_home_codex_home_and_override_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "custom-codex"
            config.mkdir()
            override = config / "AGENTS.override.md"
            override.write_text("keep override\n")
            with (
                patch("install_skill.Path.home", return_value=home),
                patch.dict(os.environ, {"CODEX_HOME": str(config)}),
            ):
                result = install(ROOT)
            self.assertEqual(
                Path(result["skill_dir"]), home / ".agents/skills/time-aware-execution"
            )
            self.assertEqual(Path(result["instructions_file"]), override)
            self.assertTrue(override.read_text().startswith("keep override\n"))
            self.assertFalse((config / "AGENTS.md").exists())

    def test_corrupt_manifest_is_not_mistaken_for_an_unchanged_install(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            result = install(ROOT, project=project)
            (Path(result["skill_dir"]) / "BUNDLE-MANIFEST.json").write_text("broken")
            with self.assertRaises(FileExistsError):
                install(ROOT, project=project)

    def test_rule_write_failure_rolls_back_new_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            rules = project / "AGENTS.md"
            rules.write_text("keep rules\n")
            with (
                patch(
                    "install_skill.os.replace", side_effect=PermissionError("blocked")
                ),
                self.assertRaises(PermissionError),
            ):
                install(ROOT, project=project)
            self.assertEqual(rules.read_text(), "keep rules\n")
            self.assertFalse((project / ".agents/skills/time-aware-execution").exists())

    def test_preserves_original_rules_and_is_idempotent(self):
        original = "# Team rules\r\nKeep customer files private.\r\n"
        first = merge_instructions(original, "Use the clock.")
        self.assertTrue(first.startswith(original))
        self.assertEqual(merge_instructions(first, "Use the clock."), first)
        second = merge_instructions(first, "Check time after work.")
        self.assertTrue(second.startswith(original))
        self.assertNotIn("Use the clock.", second)

    def test_broken_managed_markers_are_rejected(self):
        with self.assertRaises(ValueError):
            merge_instructions("<!-- time-aware-execution:begin -->", "rules")

    def test_project_install_and_repeat_do_not_mutate_existing_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            rules = project / "AGENTS.md"
            rules.write_bytes(b"# Existing\r\nKeep this instruction.\r\n")
            first = install(ROOT, project=project)
            self.assertEqual(first["status"], "installed")
            installed = Path(first["skill_dir"])
            self.assertTrue((installed / "SKILL.md").is_file())
            self.assertTrue((installed / "BUNDLE-MANIFEST.json").is_file())
            self.assertTrue(rules.read_bytes().startswith(b"# Existing\r\n"))
            before = rules.read_bytes()
            second = install(ROOT, project=project)
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(before, rules.read_bytes())
            self.assertEqual(
                first["source_files_sha256"], second["source_files_sha256"]
            )

    def test_unmanaged_existing_skill_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            target = project / ".agents/skills/time-aware-execution"
            target.mkdir(parents=True)
            (target / "custom.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                install(ROOT, project=project)
            self.assertEqual((target / "custom.txt").read_text(), "keep")
            self.assertFalse((project / "AGENTS.md").exists())

    def test_failed_selftest_does_not_publish_skill_or_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with (
                patch("install_skill.verify", side_effect=ValueError("broken")),
                self.assertRaisesRegex(ValueError, "broken"),
            ):
                install(ROOT, project=project)
            self.assertFalse((project / ".agents/skills/time-aware-execution").exists())
            self.assertFalse((project / "AGENTS.md").exists())

    def test_local_modification_requires_explicit_replacement_and_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            first = install(ROOT, project=project)
            target = Path(first["skill_dir"])
            (target / "SKILL.md").write_text("locally changed")
            with self.assertRaises(FileExistsError):
                install(ROOT, project=project)
            result = install(ROOT, project=project, replace=True)
            backup = Path(result["backup_dir"])
            self.assertEqual(
                (backup / "time-aware-execution/SKILL.md").read_text(),
                "locally changed",
            )
            self.assertFalse(backup.is_relative_to(project / ".agents/skills"))
            self.assertTrue(
                json.loads((target / "BUNDLE-MANIFEST.json").read_text())["files"]
            )
