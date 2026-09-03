from __future__ import annotations

import unittest

from agent_time_control import TimeBudgetController, TimeContract


class PublicApiTests(unittest.TestCase):
    def test_controller_and_contract_are_available_from_top_level_package(self) -> None:
        controller = TimeBudgetController(TimeContract.relative(10, reserve_seconds=2))
        self.assertEqual(controller.checkpoint()["phase"], "execute")


if __name__ == "__main__":
    unittest.main()
