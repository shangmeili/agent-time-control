from __future__ import annotations

import unittest

from mcp import Client

from agent_time_control.mcp_server import mcp


class MCPDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_inputs_are_strict_and_errors_are_actionable(self):
        async with Client(mcp) as client:
            for value in (True, "60", -1):
                with self.subTest(value=value):
                    result = await client.call_tool(
                        "start_timebox", {"duration_seconds": value}
                    )
                    self.assertTrue(result.is_error)
            result = await client.call_tool(
                "start_timebox", {"duration_seconds": 1, "reserve_seconds": 2}
            )
            self.assertTrue(result.is_error)
            self.assertIn("reserve", str(result.content))
            good = await client.call_tool("start_timebox", {"duration_seconds": 60})
            self.assertFalse(good.is_error)

    async def test_duplicates_are_rejected_through_transport(self):
        records = [
            {
                "observation_id": "duplicate",
                "reference_class": "test",
                "operation": "op",
                "outcome": "complete",
                "elapsed_seconds": 1,
            }
        ] * 5
        async with Client(mcp) as client:
            result = await client.call_tool(
                "forecast_remaining",
                {
                    "remaining_steps": ["op"],
                    "observations": records,
                    "reference_class": "test",
                },
            )
            self.assertTrue(result.is_error)
            self.assertIn("duplicate", str(result.content))
