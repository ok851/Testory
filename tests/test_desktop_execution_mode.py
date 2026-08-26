# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import MagicMock, patch

from modules.desktop.desktop_env_config import (
    deployment_profile,
    desktop_auto_start_gateway,
    desktop_execution_mode,
    is_local_deployment,
    remote_desktop_enabled,
)


class TestDesktopExecutionMode(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_local_defaults(self):
        os.environ.pop("DEPLOYMENT_PROFILE", None)
        os.environ.pop("DESKTOP_EXECUTION_MODE", None)
        self.assertEqual(deployment_profile(), "local")
        self.assertTrue(is_local_deployment())
        self.assertEqual(desktop_execution_mode(), "inprocess")
        self.assertFalse(remote_desktop_enabled())
        self.assertFalse(desktop_auto_start_gateway())

    def test_enterprise_gateway_default(self):
        os.environ["DEPLOYMENT_PROFILE"] = "enterprise"
        os.environ.pop("DESKTOP_EXECUTION_MODE", None)
        self.assertEqual(desktop_execution_mode(), "gateway")

    def test_remote_requires_enterprise(self):
        os.environ["DEPLOYMENT_PROFILE"] = "local"
        os.environ["DESKTOP_EXECUTION_MODE"] = "remote"
        self.assertFalse(remote_desktop_enabled())
        os.environ["DEPLOYMENT_PROFILE"] = "enterprise"
        self.assertTrue(remote_desktop_enabled())


class TestSyncDesktopExecuteStep(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch("desktop_automation._sync_desktop_execute_inprocess")
    def test_inprocess_mode(self, mock_inproc):
        os.environ["DESKTOP_EXECUTION_MODE"] = "inprocess"
        mock_inproc.return_value = {"status": "success"}
        from modules.desktop.desktop_automation import sync_desktop_execute_step

        out = sync_desktop_execute_step({"action": "wait", "input_value": "0.01"})
        self.assertEqual(out["status"], "success")
        mock_inproc.assert_called_once()

    @patch("desktop_automation._sync_desktop_execute_via_gateway")
    def test_gateway_mode(self, mock_gw):
        os.environ["DESKTOP_EXECUTION_MODE"] = "gateway"
        mock_gw.return_value = {"status": "success"}
        from modules.desktop.desktop_automation import sync_desktop_execute_step

        sync_desktop_execute_step({"action": "wait", "input_value": "0.01"})
        mock_gw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
