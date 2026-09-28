from __future__ import annotations

import asyncio
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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

    def test_reserve_actions_fit_hard_deadline_and_keep_work_blocked(self) -> None:
        self.clock.value = self.start + timedelta(seconds=85)
        for kind in ("verification", "handoff"):
            with self.subTest(kind=kind):
                state = self.controller.require_action_allowed(
                    estimated_seconds=10, action_kind=kind
                )
                self.assertEqual(state["phase"], "reserve")
                with self.assertRaisesRegex(NewWorkWindowClosed, "hard deadline"):
                    self.controller.require_action_allowed(
                        estimated_seconds=16, action_kind=kind
                    )
        with self.assertRaises(NewWorkWindowClosed):
            self.controller.require_action_allowed(estimated_seconds=1)
        self.clock.value = self.start + timedelta(seconds=100)
        for kind in ("work", "verification", "handoff"):
            with (
                self.subTest(expired_kind=kind),
                self.assertRaises(HardDeadlineReached),
            ):
                self.controller.require_action_allowed(action_kind=kind)

    def test_optional_work_cannot_claim_reserve_and_unknown_kind_is_rejected(
        self,
    ) -> None:
        for kind in ("verification", "handoff", "unknown"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.controller.require_action_allowed(optional=True, action_kind=kind)

    def test_contract_uses_absolute_instants_across_dst(self) -> None:
        zone = ZoneInfo("America/New_York")
        for start in (
            datetime(2026, 3, 8, 1, 30, tzinfo=zone),
            datetime(2026, 11, 1, 0, 30, tzinfo=zone),
        ):
            with self.subTest(start=start):
                contract = TimeContract.relative(7200, now=start)
                self.assertEqual(
                    contract.deadline.timestamp() - contract.started_at.timestamp(),
                    7200,
                )
        early = datetime(2026, 11, 1, 1, 50, tzinfo=zone, fold=0)
        late = datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=1)
        contract = TimeContract(early, late, reserve_seconds=600)
        self.assertEqual(
            TimeBudgetController(contract, clock=lambda: early).snapshot()[
                "remaining_seconds"
            ],
            1200,
        )
        with self.assertRaises(ValueError):
            TimeContract(late, early)

    async def test_expired_wrapper_cancels_future_and_closes_unstarted_coroutine(
        self,
    ) -> None:
        self.clock.value = self.start + timedelta(seconds=101)
        future = asyncio.get_running_loop().create_future()
        with self.assertRaises(HardDeadlineReached):
            await self.controller.run_until_hard_deadline(future)
        self.assertTrue(future.cancelled())
        called = False

        async def work() -> None:
            nonlocal called
            called = True

        coroutine = work()
        with self.assertRaises(HardDeadlineReached):
            await self.controller.run_until_hard_deadline(coroutine)
        self.assertIsNone(coroutine.cr_frame)
        self.assertFalse(called)

    async def test_cancelling_wrapper_requests_child_cancellation(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work() -> None:
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        wrapper = asyncio.create_task(self.controller.run_until_hard_deadline(work()))
        await started.wait()
        wrapper.cancel()
        await asyncio.gather(wrapper, return_exceptions=True)
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    async def test_regression_expired_wrapper_cancels_running_task(self) -> None:
        started = asyncio.Event()

        async def work() -> None:
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(work())
        await started.wait()
        self.clock.value = self.start + timedelta(seconds=101)
        try:
            with self.assertRaises(HardDeadlineReached):
                await self.controller.run_until_hard_deadline(task)
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled(), "expired wrapper left its task running")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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

    async def test_reserve_verification_through_real_runner(self) -> None:
        self.clock.value = self.start + timedelta(seconds=85)
        called: list[str] = []
        for strict in (False, True):
            with self.subTest(strict=strict):
                called.clear()
                guardrail = make_tool_input_guardrail(
                    self.controller, estimated_seconds=1, action_kind="verification"
                )

                @function_tool(tool_input_guardrails=[] if strict else [guardrail])
                def verify() -> str:
                    called.append("verified")
                    return "verified"

                model = ScriptedModel(
                    [
                        [
                            ResponseFunctionToolCall(
                                arguments="{}",
                                call_id="verify-1",
                                name="verify",
                                type="function_call",
                            )
                        ],
                        [text_output("verified handoff")],
                    ]
                )
                result = await Runner.run(
                    Agent(name="test", model=model, tools=[verify]),
                    "verify and hand off",
                    run_config=RunConfig(
                        tracing_disabled=True,
                        call_model_input_filter=make_call_model_input_filter(
                            self.controller
                        ),
                    ),
                    hooks=TimeBudgetHooks(
                        self.controller,
                        strict=strict,
                        tool_estimated_seconds={"verify": 1},
                        tool_action_kinds={"verify": "verification"},
                    ),
                )
                self.assertEqual(called, ["verified"])
                self.assertEqual(result.final_output, "verified handoff")

    async def test_guardrail_rejects_verification_at_hard_deadline(self) -> None:
        self.clock.value = self.start + timedelta(seconds=100)
        called = []
        guardrail = make_tool_input_guardrail(
            self.controller, action_kind="verification"
        )

        @function_tool(tool_input_guardrails=[guardrail])
        def verify() -> str:
            called.append("must not run")
            return "invalid"

        model = ScriptedModel(
            [
                [
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="expired-1",
                        name="verify",
                        type="function_call",
                    )
                ],
                [text_output("stopped")],
            ]
        )
        result = await Runner.run(
            Agent(name="test", model=model, tools=[verify]),
            "verify",
            run_config=RunConfig(tracing_disabled=True),
        )
        self.assertEqual(called, [])
        self.assertEqual(result.final_output, "stopped")

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
