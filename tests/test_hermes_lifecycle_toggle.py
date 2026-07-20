# -*- coding: utf-8 -*-
"""Hermes 启停状态机：快速切换不应永久卡在 starting/stopping。"""
from __future__ import annotations

import time
import unittest
from unittest import mock


class TestHermesLifecycleFlags(unittest.TestCase):
    def setUp(self):
        import hermes_service_bootstrap as hb

        self.hb = hb
        with hb._LIFECYCLE_LOCK:
            hb._STARTING = False
            hb._STOPPING = False
            hb._START_BEGAN_AT = 0.0
            hb._STOP_BEGAN_AT = 0.0
            hb._START_ERROR = ""
            hb._START_FINISHED = False
            hb._BOOTED = False

    def test_stale_stopping_unlock(self):
        hb = self.hb
        with hb._LIFECYCLE_LOCK:
            hb._STOPPING = True
            hb._STOP_BEGAN_AT = time.monotonic() - (hb._STOP_STALE_SEC + 1)
        self.assertTrue(hb._force_stale_stopping_unlock())
        self.assertFalse(hb._STOPPING)

    def test_stale_starting_unlock(self):
        hb = self.hb
        with hb._LIFECYCLE_LOCK:
            hb._STARTING = True
            hb._START_BEGAN_AT = time.monotonic() - (hb._START_STALE_SEC + 1)
            hb._BOOTED = True
        self.assertTrue(hb._force_stale_starting_unlock())
        self.assertFalse(hb._STARTING)

    def test_stop_finally_clears_stopping_on_error(self):
        hb = self.hb
        with mock.patch.object(hb, "_stop_via_official_hermes_api", side_effect=RuntimeError("boom")):
            with mock.patch.object(hb, "_stop_gateway_process"):
                with mock.patch.object(hb, "_gateway_listen_endpoint", return_value=("127.0.0.1", 8642)):
                    with mock.patch.object(hb, "_port_listening", return_value=False):
                        with mock.patch.object(hb.HermesGatewayClient, "health_check", return_value=False):
                            detail = hb.stop_hermes_gateway(clear_cdp=False, cleanup_browser=False)
        self.assertFalse(hb._STOPPING)
        self.assertTrue(detail.get("fully_stopped") or detail.get("official_error"))

    def test_status_not_stuck_stopping_when_port_down(self):
        hb = self.hb
        with hb._LIFECYCLE_LOCK:
            hb._STOPPING = True
            hb._STOP_BEGAN_AT = time.monotonic() - 5.0
        with mock.patch.object(hb.HermesGatewayClient, "is_configured", return_value=True):
            with mock.patch.object(hb.HermesGatewayClient, "health_check", return_value=False):
                with mock.patch.object(hb, "_port_listening", return_value=False):
                    with mock.patch.object(hb, "_gateway_listen_endpoint", return_value=("127.0.0.1", 8642)):
                        st = hb.get_bootstrap_status()
        self.assertFalse(st.get("stopping"))


if __name__ == "__main__":
    unittest.main()
