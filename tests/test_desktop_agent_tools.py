# -*- coding: utf-8 -*-
"""桌面 Agent 工具 / 屏幕观察重构单测。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestChatToolSchemasDesktop(unittest.TestCase):
    def test_desktop_includes_windows_tools_when_opt_in(self):
        from ai_chat_tool_loop import chat_tool_schemas

        names = [
            s["function"]["name"]
            for s in chat_tool_schemas(
                platform_type="desktop",
                allow_desktop_windows_tools=True,
                allow_hermes=False,
            )
        ]
        self.assertNotIn("wechat_send_message", names)
        self.assertIn("windows_focus_app", names)
        self.assertIn("windows_click_element", names)
        self.assertIn("windows_type_text", names)
        self.assertIn("windows_press_key", names)
        self.assertIn("windows_wait", names)
        self.assertIn("refine_test_plan", names)

    def test_desktop_default_includes_windows_tools(self):
        import os
        from ai_chat_tool_loop import chat_tool_schemas

        os.environ.pop("PLATFORM_OUTER_DESKTOP_TOOLS", None)
        names = [s["function"]["name"] for s in chat_tool_schemas(platform_type="desktop")]
        self.assertIn("windows_focus_app", names)
        self.assertIn("hermes_execute", names)

    def test_screen_tools_gated_by_flag(self):
        from ai_chat_tool_loop import chat_tool_schemas

        off = [s["function"]["name"] for s in chat_tool_schemas(platform_type="desktop", allow_screen_tools=False)]
        on = [s["function"]["name"] for s in chat_tool_schemas(platform_type="desktop", allow_screen_tools=True)]
        self.assertNotIn("get_screen_text", off)
        self.assertNotIn("get_screen_description", off)
        self.assertIn("get_screen_text", on)
        self.assertIn("get_screen_description", on)

    def test_web_without_desktop_tools(self):
        from ai_chat_tool_loop import chat_tool_schemas

        names = [s["function"]["name"] for s in chat_tool_schemas(platform_type="web", allow_hermes=False)]
        self.assertNotIn("windows_focus_app", names)
        self.assertIn("refine_test_plan", names)


class TestScreenToolsCache(unittest.TestCase):
    def test_ocr_cache_hit_skips_second_ocr(self):
        import screen_tools as st

        st.clear_ocr_cache()
        fake_png = b"\x89PNG" + b"\x00" * 2000
        calls = {"n": 0}

        def fake_ocr(png):
            calls["n"] += 1
            return [{"text": "搜索", "bbox": [10, 10, 50, 30], "confidence": 0.9}]

        with patch.object(st, "capture_for_observation", return_value=(fake_png, {"surface": "test"})), patch.object(
            st, "_ocr_blocks_uncached", side_effect=fake_ocr
        ), patch("desktop_ocr.ocr_available", return_value=True), patch(
            "desktop_ocr.engine_name", return_value="mock"
        ):
            r1 = st.get_screen_text()
            r2 = st.get_screen_text()
        self.assertTrue(r1.get("success"))
        self.assertFalse(r1.get("cached"))
        self.assertTrue(r2.get("cached"))
        self.assertEqual(calls["n"], 1)

    def test_ocr_unavailable_returns_structured_error(self):
        import screen_tools as st

        with patch("desktop_ocr.ocr_available", return_value=False), patch(
            "desktop_ocr.engine_name", return_value="none"
        ):
            r = st.get_screen_text()
        self.assertFalse(r.get("success"))
        self.assertIn("OCR", r.get("error") or "")
        self.assertTrue(r.get("suggestion"))

    def test_description_hard_truncate(self):
        import screen_tools as st

        long_text = "窗" * 500
        with patch.object(st, "capture_primary_monitor_png", return_value=b"\x89PNG" + b"\x00" * 100), patch(
            "ai_vision_local.vision_describe", return_value=long_text
        ):
            r = st.get_screen_description("测试")
        self.assertTrue(r.get("success"))
        self.assertLessEqual(len(r.get("description") or ""), 300)


class TestWindowsClickElementErrors(unittest.TestCase):
    def test_empty_description(self):
        from windows_desktop_tools import windows_click_element

        r = windows_click_element("")
        self.assertFalse(r.get("success"))
        self.assertIn("空", r.get("error") or "")

    def test_no_match_returns_structured_error(self):
        from windows_desktop_tools import windows_click_element

        with patch("windows_desktop_tools._uia_find_candidates", return_value=[]), patch(
            "screen_tools.capture_primary_monitor_png", return_value=b"\x89PNG" + b"\x00" * 100
        ), patch("windows_desktop_tools._ocr_find_candidates", return_value=[]), patch(
            "windows_desktop_tools._vlm_find_point", return_value=None
        ), patch(
            "windows_desktop_tools._screen_text_list", return_value=["通讯录", "发现", "搜索", "微信"]
        ):
            r = windows_click_element("不存在的按钮XYZ")
        self.assertFalse(r.get("success"))
        self.assertEqual(r.get("error"), "未找到元素")
        self.assertIn("搜索", r.get("screen_text") or [])
        self.assertTrue(r.get("suggestion"))

    def test_multi_candidate_no_random_click(self):
        from windows_desktop_tools import windows_click_element

        cands = [
            {"name": "搜索", "x": 10, "y": 10, "score": 0.9, "via": "uia"},
            {"name": "搜索历史", "x": 20, "y": 20, "score": 0.9, "via": "uia"},
        ]
        with patch("windows_desktop_tools._try_search_hotkey_shortcut", return_value=None), patch(
            "windows_desktop_tools._uia_find_candidates", return_value=cands
        ), patch("windows_desktop_tools._screen_text_list", return_value=["搜索", "搜索历史"]):
            r = windows_click_element("搜索")
        self.assertFalse(r.get("success"))
        self.assertIn("多个候选", r.get("error") or "")
        self.assertEqual(len(r.get("candidates") or []), 2)


class TestWindowsFocusAppAliases(unittest.TestCase):
    def test_focus_needles_wechat(self):
        from windows_desktop_tools import _focus_needles

        needles = _focus_needles("微信")
        self.assertTrue(any("weixin" in n for n in needles))
        self.assertTrue(any("wechat" in n for n in needles))

    def test_score_prefers_iconic_titled_window(self):
        from windows_desktop_tools import _score_focus_candidate

        needles = ["微信", "weixin", "wechat"]
        mini = {
            "title": "微信",
            "process": "Weixin.exe",
            "class_name": "Qt51514QWindowIcon",
            "width": 160,
            "height": 28,
            "iconic": True,
        }
        tray = {
            "title": "WxTrayIconMessageWindow",
            "process": "Weixin.exe",
            "class_name": "Qt51514WxTrayIconMessageWindowClass",
            "width": 1440,
            "height": 753,
            "iconic": False,
        }
        self.assertGreater(
            _score_focus_candidate(mini, needles),
            _score_focus_candidate(tray, needles),
        )


class TestExtractTextBlocks(unittest.TestCase):
    def test_extract_text_blocks_empty(self):
        from desktop_ocr import extract_text_blocks

        self.assertEqual(extract_text_blocks(b""), [])


class TestMcpWindowsToolsRegistered(unittest.TestCase):
    def test_desktop_kit_includes_windows(self):
        from testory_mcp.kit import mcp_kit_for_port

        class _Port:
            platform = "desktop"

            def capture(self):
                class R:
                    png_bytes = b""

                return R()

            def tap(self, locate):
                return type("T", (), {"__dict__": {}})()

            def input_text(self, locate, text):
                return type("T", (), {"__dict__": {}})()

            def assert_vision(self, condition):
                return type("T", (), {"__dict__": {}})()

        _, tools = mcp_kit_for_port(_Port())
        names = [t["name"] for t in tools]
        self.assertIn("windows_focus_app", names)
        self.assertIn("get_screen_text", names)
        self.assertIn("get_screen_description", names)


if __name__ == "__main__":
    unittest.main()
