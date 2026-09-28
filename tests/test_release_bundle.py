from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_release import build_skill
from verify_install import verify


class ReleaseBundleTests(unittest.TestCase):
    def test_reproducible_archive_has_no_private_or_build_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first, second = base / "one.zip", base / "two.zip"
            build_skill(ROOT, first)
            build_skill(ROOT, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertEqual(sum(name.endswith("/SKILL.md") for name in names), 1)
                self.assertFalse(
                    any(
                        "eval-results" in name
                        or "/.git/" in name
                        or "__pycache__" in name
                        or ".egg-info" in name
                        for name in names
                    )
                )
                archive.extractall(base / "installed")
            root = base / "installed/time-aware-execution"
            manifest = json.loads((root / "BUNDLE-MANIFEST.json").read_text())
            for name, expected in manifest["files"].items():
                self.assertEqual(
                    hashlib.sha256((root / name).read_bytes()).hexdigest(), expected
                )
            # Execute from an unrelated directory using bundled code, not the checkout.
            result = subprocess.run(
                [sys.executable, "-B", str(root / "scripts/verify_install.py")],
                cwd=base,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["manifest_verified"])
            (root / "src/agent_time_control/core.py").write_text("# modified\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify(root)

    def test_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "existing.zip"
            output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                build_skill(ROOT, output)
            self.assertEqual(output.read_bytes(), b"keep")
