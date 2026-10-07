from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_validator", ROOT / "scripts/validate_release.py"
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class ReleaseValidationTests(unittest.TestCase):
    def validate(self, *, missing_backend=False, failed_script=None):
        invoked = []

        def import_module(name):
            if missing_backend and name == "setuptools.build_meta":
                raise ImportError("No module named 'setuptools'")

        def run(command, **kwargs):
            invoked.append(command)
            failed = failed_script is not None and failed_script in command
            # Exercise aggregation without recursively running this test suite.
            stdout = "Ran 1 test\nOK\n" if "unittest" in command else ""
            return subprocess.CompletedProcess(command, int(failed), stdout, "")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "receipt"
            with (
                patch.object(
                    sys, "argv", ["validate_release", "--output", str(output)]
                ),
                patch.object(
                    validator.importlib, "import_module", side_effect=import_module
                ),
                patch.object(
                    validator.importlib.metadata, "version", return_value="test"
                ),
                patch.object(validator.subprocess, "run", side_effect=run),
                redirect_stdout(StringIO()),
            ):
                status = validator.main()
            receipt = json.loads((output / "validation.json").read_text())
        return status, receipt, invoked

    def test_missing_backend_cannot_produce_a_passing_receipt(self):
        status, receipt, invoked = self.validate(missing_backend=True)
        self.assertEqual(status, 1)
        self.assertFalse(receipt["passed"])
        self.assertIn("setuptools", receipt["checks"]["required_dependencies"]["error"])
        self.assertFalse(any("scripts/build_release.py" in call for call in invoked))

    def test_build_failure_is_part_of_validation_and_blocks_installation(self):
        status, receipt, invoked = self.validate(
            failed_script="scripts/build_release.py"
        )
        self.assertEqual(status, 1)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["distributions"]["passed"])
        self.assertFalse(
            any("scripts/verify_distributions.py" in call for call in invoked)
        )

    def test_clean_installation_failure_cannot_be_hidden_by_passing_tests(self):
        status, receipt, _ = self.validate(
            failed_script="scripts/verify_distributions.py"
        )
        self.assertEqual(status, 1)
        self.assertTrue(receipt["checks"]["unit_tests"]["passed"])
        self.assertTrue(receipt["checks"]["distributions"]["passed"])
        self.assertFalse(receipt["checks"]["clean_installation"]["passed"])
        self.assertFalse(receipt["passed"])
