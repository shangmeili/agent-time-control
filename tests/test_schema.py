from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent_time_control.core import build_snapshot, create_timebox, decide

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment,misc]


class CoreBoundaryTests(unittest.TestCase):
    def test_fractional_time_does_not_cross_deadline_early(self) -> None:
        deadline = datetime.fromisoformat("2026-09-02T12:00:00+00:00")
        before = build_snapshot(
            deadline=deadline,
            now=datetime.fromisoformat("2026-09-02T11:59:59.500000+00:00"),
        )
        after = build_snapshot(
            deadline=deadline,
            now=datetime.fromisoformat("2026-09-02T12:00:00.001000+00:00"),
        )
        self.assertEqual(before["phase"], "execute")
        self.assertEqual(before["remaining_seconds"], 0)
        self.assertEqual(after["phase"], "expired")
        self.assertEqual(after["remaining_seconds"], -1)

    def test_timebox_rejects_reserve_larger_than_total_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            create_timebox(
                duration_seconds=30,
                reserve_seconds=31,
                now=datetime.fromisoformat("2026-09-02T12:00:00+00:00"),
            )


@unittest.skipIf(Draft202012Validator is None, "install test extra for schema tests")
class CheckpointSchemaTests(unittest.TestCase):
    def test_gate_output_conforms_to_checkpoint_schema(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "time-checkpoint.schema.json").read_text(
                encoding="utf-8"
            )
        )
        snapshot = build_snapshot(
            deadline=datetime.fromisoformat("2026-09-02T13:00:00+00:00"),
            now=datetime.fromisoformat("2026-09-02T12:00:00+00:00"),
            reserve_seconds=600,
        )
        result = decide(
            snapshot,
            low_seconds=300,
            likely_seconds=600,
            high_seconds=900,
            multiplier=1.2,
        )
        Draft202012Validator(schema).validate(result)
