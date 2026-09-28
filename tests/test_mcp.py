from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

try:
    from mcp import Client, StdioServerParameters

    from agent_time_control.mcp_server import mcp
except ImportError:
    Client = None  # type: ignore[assignment,misc]
    StdioServerParameters = None  # type: ignore[assignment,misc]
    mcp = None


@unittest.skipIf(Client is None, "install the test extra to run MCP integration tests")
class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_process_discovery_and_structured_tools(self) -> None:
        assert Client is not None
        assert mcp is not None
        async with Client(mcp, raise_exceptions=True) as client:
            tools = await client.list_tools()
            self.assertEqual(
                {tool.name for tool in tools.tools},
                {
                    "time_now",
                    "start_timebox",
                    "check_deadline",
                    "evaluate_checkpoint",
                    "summarize_calibration",
                    "forecast_remaining",
                },
            )
            started = await client.call_tool(
                "start_timebox",
                {
                    "duration_seconds": 60,
                    "reserve_seconds": 10,
                    "timezone_name": "UTC",
                },
            )
            self.assertFalse(started.is_error)
            assert started.structured_content is not None
            self.assertEqual(started.structured_content["phase"], "execute")
            self.assertEqual(started.structured_content["remaining_seconds"], 60)
            self.assertEqual(
                started.structured_content["execution_remaining_seconds"], 50
            )

            stopped = await client.call_tool(
                "evaluate_checkpoint",
                {
                    "deadline": "2000-01-01T00:00:00Z",
                    "estimate_low_seconds": 0,
                    "estimate_likely_seconds": 0,
                    "estimate_high_seconds": 0,
                },
            )
            self.assertFalse(stopped.is_error)
            assert stopped.structured_content is not None
            self.assertEqual(stopped.structured_content["action"], "stop")

    async def test_forecast_tool_returns_evidence_and_missing_history(self) -> None:
        assert Client is not None and mcp is not None
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_time_control.mcp_server"],
            env={"PYTHONPATH": str(SRC)},
        )
        async with Client(params) as client:
            arguments = {
                "reference_class": "test-model-host",
                "remaining_steps": ["verify"],
                "observations": [],
            }
            missing = await client.call_tool("forecast_remaining", arguments)
            self.assertFalse(missing.is_error)
            self.assertIsNone(missing.structured_content["remaining_work_interval"])
            arguments["observations"] = [
                {
                    "reference_class": "test-model-host",
                    "operation": "verify",
                    "elapsed_seconds": seconds,
                    "outcome": "complete",
                }
                for seconds in (1, 2, 3, 4, 5)
            ]
            result = await client.call_tool("forecast_remaining", arguments)
            self.assertFalse(result.is_error)
            self.assertEqual(
                result.structured_content["remaining_work_interval"]["likely_seconds"],
                3,
            )

    async def test_timebox_refresh_and_gate_keep_original_deadline(self) -> None:
        assert Client is not None and mcp is not None
        async with Client(mcp, raise_exceptions=True) as client:
            started = await client.call_tool(
                "start_timebox", {"duration_seconds": 60, "reserve_seconds": 10}
            )
            self.assertFalse(started.is_error)
            state = started.structured_content
            assert state is not None
            arguments = {
                "deadline": state["deadline"],
                "started_at": state["started_at"],
                "reserve_seconds": 10,
            }
            checked = await client.call_tool("check_deadline", arguments)
            self.assertFalse(checked.is_error)
            refreshed = checked.structured_content
            assert refreshed is not None
            self.assertEqual(refreshed["deadline"], state["deadline"])
            self.assertEqual(refreshed["started_at"], state["started_at"])
            self.assertLessEqual(refreshed["remaining_seconds"], 60)
            self.assertGreater(refreshed["remaining_seconds"], 40)
            gate = await client.call_tool(
                "evaluate_checkpoint",
                {
                    **arguments,
                    "estimate_low_seconds": 1,
                    "estimate_likely_seconds": 2,
                    "estimate_high_seconds": 3,
                },
            )
            self.assertFalse(gate.is_error)
            assert gate.structured_content is not None
            self.assertEqual(gate.structured_content["action"], "continue")

    async def test_invalid_timebox_inputs_return_tool_errors(self) -> None:
        assert Client is not None and mcp is not None
        async with Client(mcp) as client:
            for arguments in (
                {"duration_seconds": -1},
                {"duration_seconds": 1, "reserve_seconds": 2},
                {"duration_seconds": 1, "timezone_name": "not-a-zone"},
            ):
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("start_timebox", arguments)
                    self.assertTrue(result.is_error)

    async def test_stdio_subprocess_interoperability(self) -> None:
        assert Client is not None
        assert StdioServerParameters is not None
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_time_control.mcp_server"],
            env={"PYTHONPATH": str(SRC)},
        )
        async with Client(params) as client:
            result = await client.call_tool("time_now", {"timezone_name": "UTC"})
            self.assertFalse(result.is_error)
            assert result.structured_content is not None
            self.assertEqual(
                result.structured_content["clock_source"], "host_system_clock"
            )
            self.assertEqual(result.structured_content["timezone"], "UTC")

    async def test_calibration_tool_does_not_require_file_access(self) -> None:
        assert Client is not None
        assert mcp is not None
        records = [
            {
                "task_class": "edit",
                "actual_elapsed_seconds": 120,
                "estimated_low_seconds": 60,
                "estimated_likely_seconds": 100,
                "estimated_high_seconds": 180,
                "outcome": "complete",
            },
            {
                "task_class": "edit",
                "actual_elapsed_seconds": 300,
                "estimated_likely_seconds": 150,
                "outcome": "timed_out",
            },
        ]
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.call_tool(
                "summarize_calibration",
                {"records": records, "task_class": "edit"},
            )
            self.assertFalse(result.is_error)
            assert result.structured_content is not None
            self.assertEqual(result.structured_content["records"], 2)
            self.assertEqual(
                result.structured_content["completed_interval_coverage"], 1.0
            )
            self.assertIn(
                "survivorship bias", result.structured_content["confidence_note"]
            )
