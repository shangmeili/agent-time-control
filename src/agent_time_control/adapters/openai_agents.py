"""OpenAI Agents SDK adapter.

Install with ``pip install agent-time-control[openai-agents]``. The input filter
refreshes and injects the time state before every model call. Function-tool input
guardrails provide recoverable budget rejection; strict hooks remain available
for fail-closed tool classes. Wrap ``Runner.run(...)`` with
``controller.run_until_hard_deadline(...)`` for a local hard deadline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.run import CallModelData, ModelInputData
from agents.tool_guardrails import ToolGuardrailFunctionOutput, tool_input_guardrail

from agents import RunHooks

from ..controller import (
    HardDeadlineReached,
    NewWorkWindowClosed,
    TimeBudgetController,
)


class TimeBudgetHooks(RunHooks[Any]):
    def __init__(
        self,
        controller: TimeBudgetController,
        *,
        strict: bool = False,
        tool_estimated_seconds: dict[str, float] | None = None,
        optional_tool_names: set[str] | None = None,
    ) -> None:
        self.controller = controller
        self.strict = strict
        self.tool_estimated_seconds = tool_estimated_seconds or {}
        self.optional_tool_names = optional_tool_names or set()
        self.last_state: dict[str, object] | None = None

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        self.last_state = self.controller.checkpoint()
        if not self.strict:
            return
        tool_name = getattr(tool, "name", "")
        self.controller.require_action_allowed(
            estimated_seconds=self.tool_estimated_seconds.get(tool_name, 0.0),
            optional=tool_name in self.optional_tool_names,
        )


def make_tool_input_guardrail(
    controller: TimeBudgetController,
    *,
    estimated_seconds: float = 0.0,
    optional: bool = False,
    name: str = "time_budget_guardrail",
    on_reject: Callable[[str], None] | None = None,
):
    """Create a model-visible, recoverable budget rejection for a function tool."""

    @tool_input_guardrail(name=name)
    def check_time_budget(data: Any) -> ToolGuardrailFunctionOutput:
        try:
            state = controller.require_action_allowed(
                estimated_seconds=estimated_seconds,
                optional=optional,
            )
        except (HardDeadlineReached, NewWorkWindowClosed) as exc:
            message = (
                "TIME_BUDGET_REJECTED: "
                f"{exc}. Do not retry this optional or non-fitting action; "
                "use the remaining time for the required core, verification, or handoff."
            )
            if on_reject is not None:
                on_reject(message)
            return ToolGuardrailFunctionOutput.reject_content(message)
        return ToolGuardrailFunctionOutput.allow(output_info=state)

    return check_time_budget


def make_call_model_input_filter(controller: TimeBudgetController):
    """Build a RunConfig filter that injects live state before each model call."""

    def inject(data: CallModelData[Any]) -> ModelInputData:
        existing = data.model_data.instructions or ""
        instructions = f"{existing}\n\n{controller.model_context()}".strip()
        return ModelInputData(
            input=data.model_data.input,
            instructions=instructions,
        )

    return inject
