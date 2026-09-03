from __future__ import annotations

import asyncio
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent_time_control.controller import (
    HardDeadlineReached,
    NewWorkWindowClosed,
    TimeBudgetController,
    TimeContract,
)

try:
    from agents.models.interface import Model
    from agents.run import CallModelData, ModelInputData
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )

    from agent_time_control.adapters.openai_agents import (
        TimeBudgetHooks,
        make_call_model_input_filter,
        make_tool_input_guardrail,
    )
    from agents import (
        Agent,
        ModelResponse,
        RunConfig,
        Runner,
        Usage,
        UserError,
        function_tool,
    )
except ImportError:
    Agent = None  # type: ignore[assignment,misc]


if Agent is not None:

    class ScriptedModel(Model):
        def __init__(self, outputs, before_return=None) -> None:
            self.outputs = list(outputs)
            self.before_return = before_return
            self.system_instructions: list[str | None] = []

        async def get_response(self, system_instructions, *args, **kwargs):
            self.system_instructions.append(system_instructions)
            if self.before_return is not None:
                self.before_return()
            return ModelResponse(
                output=self.outputs.pop(0),
                usage=Usage(),
                response_id=None,
            )

        async def stream_response(self, *args, **kwargs):
            if False:
                yield None

    def text_output(text: str):
        return ResponseOutputMessage(
            id="message-1",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text=text,
                    type="output_text",
                )
            ],
            role="assistant",
            status="completed",
            type="message",
        )


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class TimeBudgetControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.start = datetime.fromisoformat("2026-09-02T12:00:00+00:00")
        self.clock = MutableClock(self.start)
        self.contract = TimeContract(
            started_at=self.start,
            deadline=self.start + timedelta(seconds=100),
            reserve_seconds=20,
        )
        self.controller = TimeBudgetController(self.contract, clock=self.clock)

    def test_progressive_forecast_changes_automatic_control_action(self) -> None:
        initial = self.controller.update_forecast(10, 20, 30)
        self.assertEqual(initial["action"], "continue")

        self.clock.value = self.start + timedelta(seconds=60)
        later = self.controller.checkpoint()
        self.assertEqual(later["action"], "continue_core_only")

        self.clock.value = self.start + timedelta(seconds=75)
        constrained = self.controller.checkpoint()
        self.assertEqual(constrained["action"], "reduce_scope_or_handoff")

    def test_new_work_is_blocked_during_reserve_and_after_deadline(self) -> None:
        self.clock.value = self.start + timedelta(seconds=81)
        with self.assertRaises(NewWorkWindowClosed):
            self.controller.require_new_work_allowed()

        self.clock.value = self.start + timedelta(seconds=101)
        with self.assertRaises(HardDeadlineReached):
            self.controller.require_new_work_allowed()

    def test_action_must_fit_and_optional_work_obeys_scope_gate(self) -> None:
        self.controller.update_forecast(10, 20, 30)
        self.clock.value = self.start + timedelta(seconds=60)
        self.controller.require_action_allowed(estimated_seconds=10, optional=False)
        with self.assertRaisesRegex(NewWorkWindowClosed, "optional work"):
            self.controller.require_action_allowed(estimated_seconds=10, optional=True)
        with self.assertRaisesRegex(NewWorkWindowClosed, "does not fit"):
            self.controller.require_action_allowed(estimated_seconds=21, optional=False)

    def test_optional_work_requires_a_current_forecast(self) -> None:
        with self.assertRaisesRegex(NewWorkWindowClosed, "requires a current"):
            self.controller.require_action_allowed(estimated_seconds=1, optional=True)

        self.controller.update_forecast(10, 20, 30)
        self.controller.require_action_allowed(estimated_seconds=1, optional=True)

    def test_invalid_forecast_does_not_replace_last_valid_state(self) -> None:
        valid = self.controller.update_forecast(10, 20, 30)
        with self.assertRaises(ValueError):
            self.controller.update_forecast(30, 20, 10)
        after = self.controller.checkpoint()
        self.assertEqual(
            after["remaining_work_interval"],
            {
                "low_seconds": 10.0,
                "likely_seconds": 20.0,
                "high_seconds": 30.0,
            },
        )
        self.assertEqual(after["action"], valid["action"])

    async def test_hard_deadline_cancels_local_awaitable(self) -> None:
        contract = TimeContract.relative(duration_seconds=0.05)
        controller = TimeBudgetController(contract)
        cancelled = asyncio.Event()

        async def slow() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        with self.assertRaises(HardDeadlineReached):
            await controller.run_until_hard_deadline(slow())
        self.assertTrue(cancelled.is_set())

    async def test_hard_deadline_returns_without_waiting_for_cancel_suppression(
        self,
    ) -> None:
        contract = TimeContract.relative(duration_seconds=0.05)
        controller = TimeBudgetController(contract)
        cleanup_finished = asyncio.Event()

        async def cancellation_resistant() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)
                cleanup_finished.set()

        started = time.monotonic()
        with self.assertRaises(HardDeadlineReached):
            await controller.run_until_hard_deadline(cancellation_resistant())
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertFalse(cleanup_finished.is_set())
        await asyncio.wait_for(cleanup_finished.wait(), timeout=1)


@unittest.skipIf(Agent is None, "install openai-agents extra for adapter tests")
class OpenAIAgentsAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.start = datetime.fromisoformat("2026-09-02T12:00:00+00:00")
        self.clock = MutableClock(self.start)
        self.controller = TimeBudgetController(
            TimeContract(
                started_at=self.start,
                deadline=self.start + timedelta(seconds=100),
                reserve_seconds=20,
            ),
            clock=self.clock,
        )
        self.controller.update_forecast(10, 20, 30)

    def test_model_input_filter_injects_refreshed_state(self) -> None:
        assert Agent is not None
        agent = Agent(name="test")
        data = CallModelData(
            model_data=ModelInputData(input=[], instructions="Base instructions"),
            agent=agent,
            context=None,
        )
        inject = make_call_model_input_filter(self.controller)
        first = inject(data)
        self.assertIn('"phase":"execute"', first.instructions or "")

        self.clock.value = self.start + timedelta(seconds=85)
        second = inject(data)
        self.assertIn('"phase":"reserve"', second.instructions or "")

    async def test_hook_rejects_tool_start_in_reserve(self) -> None:
        self.clock.value = self.start + timedelta(seconds=85)
        hooks = TimeBudgetHooks(self.controller, strict=True)
        with self.assertRaises(NewWorkWindowClosed):
            await hooks.on_tool_start(None, None, None)

    async def test_real_runner_applies_filter_without_network(self) -> None:
        model = ScriptedModel([[text_output("done")]])
        agent = Agent(name="test", model=model, instructions="Base")
        result = await Runner.run(
            agent,
            "work",
            run_config=RunConfig(
                call_model_input_filter=make_call_model_input_filter(self.controller),
                tracing_disabled=True,
            ),
            hooks=TimeBudgetHooks(self.controller),
        )
        self.assertEqual(result.final_output, "done")
        self.assertEqual(len(model.system_instructions), 1)
        self.assertIn("TIME_CONTROL_STATE=", model.system_instructions[0] or "")

    async def test_real_runner_blocks_tool_after_window_closes(self) -> None:
        called = False

        @function_tool
        def optional_work() -> str:
            nonlocal called
            called = True
            return "should not run"

        def enter_reserve() -> None:
            self.clock.value = self.start + timedelta(seconds=85)

        model = ScriptedModel(
            [
                [
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="call-1",
                        name="optional_work",
                        type="function_call",
                    )
                ]
            ],
            before_return=enter_reserve,
        )
        agent = Agent(name="test", model=model, tools=[optional_work])
        with self.assertRaisesRegex(UserError, "execution window closed"):
            await Runner.run(
                agent,
                "work",
                run_config=RunConfig(
                    call_model_input_filter=make_call_model_input_filter(
                        self.controller
                    ),
                    tracing_disabled=True,
                ),
                hooks=TimeBudgetHooks(self.controller, strict=True),
            )
        self.assertFalse(called)

    async def test_real_runner_blocks_optional_tool_before_reserve(self) -> None:
        called = False

        @function_tool
        def optional_work() -> str:
            nonlocal called
            called = True
            return "should not run"

        self.clock.value = self.start + timedelta(seconds=60)
        model = ScriptedModel(
            [
                [
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="call-optional",
                        name="optional_work",
                        type="function_call",
                    )
                ]
            ]
        )
        agent = Agent(name="test", model=model, tools=[optional_work])
        hooks = TimeBudgetHooks(
            self.controller,
            strict=True,
            tool_estimated_seconds={"optional_work": 10},
            optional_tool_names={"optional_work"},
        )
        with self.assertRaisesRegex(UserError, "optional work is prohibited"):
            await Runner.run(
                agent,
                "work",
                run_config=RunConfig(
                    call_model_input_filter=make_call_model_input_filter(
                        self.controller
                    ),
                    tracing_disabled=True,
                ),
                hooks=hooks,
            )
        self.assertFalse(called)

    async def test_guardrail_rejects_tool_but_allows_handoff_response(self) -> None:
        called = False
        rejections: list[str] = []
        guardrail = make_tool_input_guardrail(
            self.controller,
            estimated_seconds=10,
            optional=True,
            on_reject=rejections.append,
        )

        @function_tool(tool_input_guardrails=[guardrail])
        def optional_work() -> str:
            nonlocal called
            called = True
            return "should not run"

        self.clock.value = self.start + timedelta(seconds=60)
        model = ScriptedModel(
            [
                [
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="call-recoverable",
                        name="optional_work",
                        type="function_call",
                    )
                ],
                [text_output("verified handoff")],
            ]
        )
        agent = Agent(name="test", model=model, tools=[optional_work])
        result = await Runner.run(
            agent,
            "work",
            run_config=RunConfig(
                call_model_input_filter=make_call_model_input_filter(self.controller),
                tracing_disabled=True,
            ),
            hooks=TimeBudgetHooks(self.controller),
        )
        self.assertFalse(called)
        self.assertEqual(result.final_output, "verified handoff")
        self.assertEqual(len(rejections), 1)
        self.assertIn("TIME_BUDGET_REJECTED", rejections[0])
