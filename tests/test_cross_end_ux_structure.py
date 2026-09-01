# -*- coding: utf-8 -*-
"""多端联动：录制层字段、UI 树 unwrap、DOM 缓存失效、scrcpy 关键帧回退、双手并行判定。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestActionRecorderMobileExpand(unittest.TestCase):
    def test_mobile_run_steps_export_has_android_layer(self):
        from modules.ai.ai_action_recorder import ActionRecorder
        from modules.ai.ai_chat_tool_loop import _action_records_from_recorder_capture

        rec = ActionRecorder(platform="web")
        new = rec.capture_from_tool_event(
            name="mobile_run_steps",
            args={
                "steps": [
                    {"action": "tap", "stepDescription": "点击登录"},
                    {"action": "input", "text": "1234", "stepDescription": "输入验证码"},
                ]
            },
            result={"success": True, "results": [{"success": True}, {"success": True}]},
            status="ok",
        )
        self.assertTrue(new)
        rows = _action_records_from_recorder_capture(new)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(any(r.get("automation_layer") == "android" for r in rows))
        self.assertTrue(any(r.get("status") == "success" for r in rows))


class TestMobileUiTreeUnwrap(unittest.TestCase):
    def test_agent_http_nested_tree_xml(self):
        from modules.mobile import mobile_ui_probe as probe

        fake = {
            "success": True,
            "tree": {
                "xml": '<hierarchy><node text="登录" class="android.widget.Button" bounds="[0,0][10,10]"/></hierarchy>'
            },
        }

        def _fake_rpc(_serial):
            return {}

        with patch.dict("sys.modules", {"mobile_automation_gateway.plugin_rpc": MagicMock(get_page_source=_fake_rpc)}):
            with patch(
                "modules.mobile.mobile_agent_client.agent_page_source",
                return_value=fake,
            ):
                with patch.object(probe, "_adb_uiautomator_dump", return_value=""):
                    res = probe.get_mobile_ui_tree("SERIAL", max_nodes=20)
        self.assertTrue(res.get("success"), res)
        self.assertEqual(res.get("source"), "agent_http")
        self.assertIn("登录", res.get("compact_text") or res.get("xml") or "")


class TestDomCacheInvalidate(unittest.TestCase):
    def test_invalidate_clears_cache(self):
        from modules.ai import ai_external_browser_bridge as br

        br._dom_cache["ctx|http://x|t"] = {"ts": 0, "value": "OLD"}
        br.invalidate_dom_cache()
        self.assertEqual(br._dom_cache, {})


class TestScrcpyKeyframeFallback(unittest.TestCase):
    def test_capture_falls_back_to_adb_when_keyframe_none(self):
        from modules.mobile import mobile_scrcpy_vision as vis

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 200
        with patch(
            "modules.mobile.mobile_scrcpy_bridge.get_latest_keyframe_png",
            return_value=None,
        ):
            with patch("subprocess.run") as run:
                run.return_value = MagicMock(returncode=0, stdout=png, stderr=b"")
                with patch.object(vis, "adb_path", create=True):
                    # capture imports adb_path inside
                    with patch(
                        "modules.mobile.mobile_env_config.adb_path",
                        return_value="adb",
                    ):
                        out = vis.capture_device_frame("emu-1", timeout=2)
        self.assertEqual(out, png)
        self.assertEqual(vis.get_last_capture_source("emu-1"), "adb_screencap")

    def test_capture_prefers_keyframe(self):
        from modules.mobile import mobile_scrcpy_vision as vis

        png = b"\x89PNG\r\n\x1a\n" + b"K" * 200
        with patch(
            "modules.mobile.mobile_scrcpy_bridge.get_latest_keyframe_png",
            return_value=png,
        ):
            out = vis.capture_device_frame("emu-2", timeout=2)
        self.assertEqual(out, png)
        self.assertEqual(vis.get_last_capture_source("emu-2"), "scrcpy_keyframe")


class TestHandsParallelGate(unittest.TestCase):
    def test_parallel_ok_for_independent_pc_mobile(self):
        from modules.ai.ai_chat_tool_loop import _hands_parallel_ok

        self.assertTrue(
            _hands_parallel_ok(
                "windows_click_element",
                {"description": "确定"},
                "mobile_get_ui_tree",
                {},
                {},
            )
        )

    def test_parallel_blocked_for_otp_chain(self):
        from modules.ai.ai_chat_tool_loop import _hands_parallel_ok

        self.assertFalse(
            _hands_parallel_ok(
                "mobile_extract_otp",
                {},
                "windows_type_text",
                {"text": "{{sms_otp}}"},
                {},
            )
        )


class TestWindowsGetUiTreeRegistered(unittest.TestCase):
    def test_in_windows_tool_names(self):
        from modules.desktop.windows_desktop_tools import WINDOWS_TOOL_NAMES

        self.assertIn("windows_get_ui_tree", WINDOWS_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
