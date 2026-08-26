# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from modules.execution.execution_factory import ExecutorFactory, get_executor_factory


class TestExecutionFactory(unittest.TestCase):
    def test_singleton(self):
        self.assertIs(get_executor_factory(), get_executor_factory())

    def test_case_includes_desktop(self):
        f = ExecutorFactory()
        steps = [{"automation_layer": "web"}, {"automation_layer": "desktop"}]
        self.assertTrue(f.case_includes_desktop(steps))

    @patch("execution_factory.sync_desktop_execute_step")
    def test_execute_desktop_step(self, mock_sync):
        mock_sync.return_value = {
            "status": "success",
            "action": "click",
            "verified": True,
            "pointer_executed": True,
        }
        f = ExecutorFactory()
        out = f.execute_desktop_step(
            {"automation_layer": "desktop", "action": "click"},
            selector_value="btn1",
        )
        self.assertEqual(out["status"], "success")
        mock_sync.assert_called_once()

    def test_execute_web_delegates(self):
        f = ExecutorFactory()
        called = []

        def web_cb(step):
            called.append(step)

        out = f.execute_step(
            {"automation_layer": "web", "action": "navigate"},
            web_executor=web_cb,
            input_value="https://example.com",
        )
        self.assertEqual(out["status"], "delegated_web")
        self.assertEqual(len(called), 1)


if __name__ == "__main__":
    unittest.main()
