from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "run_ollama.py"
SPEC = importlib.util.spec_from_file_location("run_ollama", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_ollama = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_ollama
SPEC.loader.exec_module(run_ollama)


class OllamaRunnerTests(unittest.TestCase):
    def test_acceptance_scoring_does_not_require_optional_scope(self) -> None:
        case = run_ollama.CASES[0]
        required_only = f"{case.core_fact} {case.verification_receipt}"
        with_optional = f"{required_only} {case.optional_fact}"
        self.assertEqual(run_ollama.score_output(case, required_only), 0.9)
        self.assertEqual(run_ollama.score_output(case, with_optional), 1.0)

    def test_prompt_prioritizes_required_acceptance_over_optional_scope(self) -> None:
        prompt = run_ollama.user_prompt(run_ollama.CASES[0], 20)
        self.assertIn("within 20.0 seconds", prompt)
        self.assertIn("Required acceptance criteria", prompt)
        self.assertIn("Optional:", prompt)

    def test_sample_seed_is_paired_across_conditions(self) -> None:
        case = run_ollama.CASES[1]
        seed = run_ollama.paired_sample_seed(100, case, repetition=2)
        self.assertEqual(seed, 107)
        self.assertEqual(
            seed,
            run_ollama.paired_sample_seed(100, case, repetition=2),
        )

    def test_cases_exercise_distinct_latency_profiles(self) -> None:
        profiles = {
            run_ollama.effective_delays(case, 0.4, 2.0, 0.4)
            for case in run_ollama.CASES
        }
        self.assertEqual(len(profiles), len(run_ollama.CASES))

    def test_action_selection_requires_exactly_one_available_action(self) -> None:
        available = ["verify_core_fact", "get_optional_fact"]
        self.assertEqual(
            run_ollama.parse_action_selection("verify_core_fact", available),
            "verify_core_fact",
        )
        with self.assertRaises(ValueError):
            run_ollama.parse_action_selection(
                "verify_core_fact or get_optional_fact", available
            )

    def test_remaining_work_estimate_requires_three_ordered_numbers(self) -> None:
        self.assertEqual(
            run_ollama.parse_remaining_work_estimate("1.5 2 4"),
            (1.5, 2.0, 4.0),
        )
        for invalid in ("1 2", "3 2 1", "-1 2 3", "1 2 3 4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                run_ollama.parse_remaining_work_estimate(invalid)
