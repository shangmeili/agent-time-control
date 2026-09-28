from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "run_ollama.py"
SPEC = importlib.util.spec_from_file_location("run_ollama", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_ollama = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_ollama
SPEC.loader.exec_module(run_ollama)

try:
    import agents
except ImportError:
    agents = None


@unittest.skipIf(agents is None, "install openai-agents extra for workflow tests")
class OllamaWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_does_not_count_a_model_call_after_direct_verification(self):
        with patch.object(
            agents.Runner,
            "run",
            side_effect=[
                SimpleNamespace(final_output="get_optional_fact"),
                SimpleNamespace(final_output="report_checkpoint"),
                SimpleNamespace(final_output="0 0 0"),
            ],
        ) as runner:
            result = await run_ollama.run_case(
                case=run_ollama.CASES[0],
                condition="tracked",
                model_name="offline-scripted",
                host="http://127.0.0.1:1",
                budget_seconds=5,
                reserve_seconds=2,
                core_delay=0,
                optional_delay=0,
                verify_delay=0,
                max_turns=6,
                sample_seed=1,
            )
        self.assertEqual(result["outcome"], "complete", result)
        self.assertEqual(runner.call_count, 3)
        self.assertIn('"required_only":0', runner.call_args_list[-1].args[1])

    async def test_rejected_optional_call_is_recorded_without_execution(self) -> None:
        with patch.object(
            agents.Runner,
            "run",
            side_effect=[
                SimpleNamespace(final_output="get_optional_fact"),
                SimpleNamespace(final_output="verify_core_fact"),
            ],
        ):
            result = await run_ollama.run_case(
                case=run_ollama.CASES[0],
                condition="controller",
                model_name="offline-scripted",
                host="http://127.0.0.1:1",
                budget_seconds=5,
                reserve_seconds=2,
                core_delay=0,
                optional_delay=0,
                verify_delay=0,
                max_turns=5,
                sample_seed=1,
            )
        self.assertEqual(result["outcome"], "complete", result)
        self.assertEqual(result["verified_utility"], 0.9)
        rejected = result["rejected_tool_events"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["tool"], "get_optional_fact")
        executed_ids = {event.get("call_id") for event in result["tool_events"]}
        self.assertTrue(rejected[0]["call_id"])
        self.assertNotIn(rejected[0]["call_id"], executed_ids)
        self.assertNotIn(
            "get_optional_fact", {event["tool"] for event in result["tool_events"]}
        )

    async def test_regression_verification_finishes_in_reserve(self) -> None:
        start = datetime.now(timezone.utc)
        clock = [start]
        controller = run_ollama.TimeBudgetController(
            run_ollama.TimeContract.relative(5, reserve_seconds=2, now=start),
            clock=lambda: clock[0],
        )

        async def select_action(*args, **kwargs):
            clock[0] = start + timedelta(seconds=3.2)
            return SimpleNamespace(final_output="verify_core_fact")

        with (
            patch.object(run_ollama, "TimeBudgetController", return_value=controller),
            patch.object(agents.Runner, "run", side_effect=select_action),
        ):
            result = await run_ollama.run_case(
                case=run_ollama.CASES[0],
                condition="controller",
                model_name="offline-scripted",
                host="http://127.0.0.1:1",
                budget_seconds=5,
                reserve_seconds=2,
                core_delay=0,
                optional_delay=0,
                verify_delay=0.05,
                max_turns=4,
                sample_seed=1,
            )
        self.assertEqual(result["outcome"], "complete", result)
        self.assertEqual(result["verified_utility"], 0.9)
        self.assertEqual(result["rejected_tool_events"], [])


class OllamaRunnerTests(unittest.TestCase):
    def test_estimation_prompt_exposes_progress_and_runtime_evidence(self):
        prompt = run_ollama.estimation_prompt(
            completed_steps=[{"operation": "core", "elapsed_seconds": 1.2}],
            required_steps=["verify"],
            optional_steps=[],
            nominal_step_seconds={"verify": 0.4},
            model_call_seconds=[0.8, 1.0],
        )
        self.assertIn('"remaining_required_steps":["verify"]', prompt)
        self.assertIn('"observed_model_call_seconds":[0.8,1.0]', prompt)
        self.assertIn("after this estimate returns", prompt)
        self.assertIn("NOT a duration estimate", prompt)

    def test_estimate_parser_accepts_existing_labelled_formats(self):
        for text in (
            "low likely high 20 30 50",
            "low:20 likely:30 high:50",
            "[20,30,50]",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    run_ollama.parse_remaining_work_estimate(text), (20, 30, 50)
                )

    def test_estimate_parser_preserves_scientific_notation(self):
        self.assertEqual(
            run_ollama.parse_remaining_work_estimate("1e-3 2e-3 3e-3"),
            (0.001, 0.002, 0.003),
        )
        for invalid in ("budget 20 low 1 high 2", "1e999 1e999 1e999"):
            with self.assertRaises(ValueError):
                run_ollama.parse_remaining_work_estimate(invalid)

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
