# -*- coding: utf-8 -*-
"""桌面 Agent 工具 / 屏幕观察重构单测。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestChatToolSchemasDesktop(unittest.TestCase):
    def test_desktop_includes_windows_tools_when_opt_in(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

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
        self.assertIn("windows_launch_app", names)
        self.assertIn("windows_click_element", names)
        self.assertIn("windows_type_text", names)
        self.assertIn("windows_press_key", names)
        self.assertIn("windows_wait", names)
        # 桌面精简 profile：不暴露 refine / hermes，避免工具淹没
        self.assertNotIn("refine_test_plan", names)
        self.assertNotIn("hermes_execute", names)

    def test_desktop_default_includes_windows_tools(self):
        import os
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        os.environ.pop("PLATFORM_OUTER_DESKTOP_TOOLS", None)
        names = [s["function"]["name"] for s in chat_tool_schemas(platform_type="desktop")]
        self.assertIn("windows_focus_app", names)
        self.assertIn("get_screen_text", names)
        self.assertNotIn("hermes_execute", names)

    def test_screen_tools_on_desktop_by_default(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        names = [
            s["function"]["name"]
            for s in chat_tool_schemas(platform_type="desktop", allow_screen_tools=False)
        ]
        # 桌面精简 profile 自带观察工具
        self.assertIn("get_screen_text", names)
        self.assertIn("get_screen_description", names)

    def test_web_without_desktop_tools(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        names = [s["function"]["name"] for s in chat_tool_schemas(platform_type="web", allow_hermes=False)]
        self.assertNotIn("windows_focus_app", names)
        self.assertIn("refine_test_plan", names)


class TestScreenToolsCache(unittest.TestCase):
    def test_ocr_cache_hit_skips_second_ocr(self):
        from modules.ai import screen_tools as st

        st.clear_ocr_cache()
        fake_png = b"\x89PNG" + b"\x00" * 2000
        calls = {"n": 0}

        def fake_ocr(png):
            calls["n"] += 1
            return [{"text": "搜索", "bbox": [10, 10, 50, 30], "confidence": 0.9}]

        with patch.object(st, "capture_for_observation", return_value=(fake_png, {"surface": "test"})), patch.object(
            st, "_ocr_blocks_uncached", side_effect=fake_ocr
        ), patch("modules.desktop.desktop_ocr.ocr_available", return_value=True), patch(
            "modules.desktop.desktop_ocr.engine_name", return_value="mock"
        ):
            r1 = st.get_screen_text()
            r2 = st.get_screen_text()
        self.assertTrue(r1.get("success"))
        self.assertFalse(r1.get("cached"))
        self.assertTrue(r2.get("cached"))
        self.assertEqual(calls["n"], 1)

    def test_ocr_unavailable_returns_structured_error(self):
        from modules.ai import screen_tools as st

        with patch("modules.desktop.desktop_ocr.ocr_available", return_value=False), patch(
            "modules.desktop.desktop_ocr.engine_name", return_value="none"
        ):
            r = st.get_screen_text()
        self.assertFalse(r.get("success"))
        self.assertIn("OCR", r.get("error") or "")
        self.assertTrue(r.get("suggestion"))

    def test_description_hard_truncate(self):
        from modules.ai import screen_tools as st

        long_text = "窗" * 500
        with patch.object(st, "capture_primary_monitor_png", return_value=b"\x89PNG" + b"\x00" * 100), patch(
            "modules.ai.ai_vision_local.vision_describe", return_value=long_text
        ):
            r = st.get_screen_description("测试")
        self.assertTrue(r.get("success"))
        self.assertLessEqual(len(r.get("description") or ""), 300)


class TestWindowsClickElementErrors(unittest.TestCase):
    def test_empty_description(self):
        from modules.desktop.windows_desktop_tools import windows_click_element

        r = windows_click_element("")
        self.assertFalse(r.get("success"))
        self.assertIn("空", r.get("error") or "")

    def test_no_match_returns_structured_error(self):
        from modules.desktop.windows_desktop_tools import windows_click_element

        with patch("modules.desktop.windows_desktop_tools._uia_find_candidates", return_value=[]), patch(
            "modules.ai.screen_tools.capture_primary_monitor_png", return_value=b"\x89PNG" + b"\x00" * 100
        ), patch("modules.desktop.windows_desktop_tools._ocr_find_candidates", return_value=[]), patch(
            "modules.desktop.windows_desktop_tools._vlm_find_point", return_value=None
        ), patch(
            "modules.desktop.windows_desktop_tools._screen_text_list", return_value=["通讯录", "发现", "搜索", "微信"]
        ):
            r = windows_click_element("不存在的按钮XYZ")
        self.assertFalse(r.get("success"))
        self.assertEqual(r.get("error"), "未找到元素")
        self.assertIn("搜索", r.get("screen_text") or [])
        self.assertTrue(r.get("suggestion"))

    def test_multi_candidate_picks_instead_of_halt(self):
        """多候选时择优点击，不再直接 flow_halt（避免模型空转）。"""
        from modules.desktop.windows_desktop_tools import windows_click_element

        cands = [
            {"name": "确定", "x": 10, "y": 40, "score": 0.9, "via": "uia"},
            {"name": "确定", "x": 20, "y": 200, "score": 0.9, "via": "uia"},
        ]
        with (
            patch("modules.desktop.windows_desktop_tools._try_search_hotkey_shortcut", return_value=None),
            patch("modules.desktop.windows_desktop_tools._uia_find_candidates", return_value=cands),
            patch("modules.desktop.windows_desktop_tools._ocr_find_candidates", return_value=[]),
            patch(
                "modules.ai.screen_tools.capture_for_observation",
                return_value=(b"", {"left": 0, "top": 0}),
            ),
            patch("modules.desktop.windows_desktop_tools._screen_text_list", return_value=["确定"]),
            patch("modules.desktop.windows_desktop_tools._capture_target_hash", return_value="h0"),
            patch("modules.desktop.windows_desktop_tools._wait_stable_quiet", return_value=None),
            patch(
                "modules.desktop.windows_desktop_tools.capture_after_action",
                return_value={"ok": True, "changed": True},
            ),
            patch("modules.desktop.windows_desktop_tools._nearby_texts", return_value=["确定"]),
            patch("modules.desktop.desktop_input.force_focus_hwnd", return_value=True),
            patch("modules.desktop.desktop_input.message_click_at_screen", return_value=None),
            patch("modules.desktop.desktop_input.screen_click", return_value=None),
            patch("modules.desktop.windows_desktop_tools.get_desktop_target", return_value={"hwnd": 1, "label": "App"}),
            patch("modules.desktop.windows_desktop_tools.time.sleep", return_value=None),
        ):
            r = windows_click_element("确定")
        self.assertTrue(r.get("success"), r)
        self.assertIn(int(r.get("y") or 0), (40, 200))

    def test_generate_case_flag_controls_refine_schema(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        with_refine = [
            s["function"]["name"]
            for s in chat_tool_schemas(allow_hermes=False, allow_refine_test_plan=True)
        ]
        without = [
            s["function"]["name"]
            for s in chat_tool_schemas(allow_hermes=False, allow_refine_test_plan=False)
        ]
        self.assertIn("refine_test_plan", with_refine)
        self.assertNotIn("refine_test_plan", without)


class TestWindowsFocusAppAliases(unittest.TestCase):
    def test_focus_needles_wechat(self):
        from modules.desktop.windows_desktop_tools import _focus_needles

        needles = _focus_needles("微信")
        self.assertTrue(any("weixin" in n for n in needles))
        self.assertTrue(any("wechat" in n for n in needles))

    def test_score_prefers_iconic_titled_window(self):
        from modules.desktop.windows_desktop_tools import _score_focus_candidate

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
        from modules.desktop.desktop_ocr import extract_text_blocks

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
        self.assertIn("windows_launch_app", names)
        self.assertIn("get_screen_text", names)
        self.assertIn("get_screen_description", names)


class TestWindowsLaunchApp(unittest.TestCase):
    def test_schema_includes_launch_app(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        names = [
            s["function"]["name"]
            for s in chat_tool_schemas(
                platform_type="desktop",
                allow_desktop_windows_tools=True,
                allow_hermes=False,
            )
        ]
        self.assertIn("windows_launch_app", names)

    def test_resolve_launch_aliases(self):
        from modules.desktop.windows_desktop_tools import _resolve_launch_input

        self.assertEqual(_resolve_launch_input("记事本")[0], "notepad")
        self.assertEqual(_resolve_launch_input("Notepad")[0], "notepad")
        self.assertEqual(_resolve_launch_input("计算器")[0], "calc")

    def test_focus_miss_auto_launches(self):
        from modules.desktop import windows_desktop_tools as wdt

        miss = {
            "success": False,
            "error": "未找到「Notepad」对应窗口",
            "can_launch": True,
            "suggestion": "请调用 windows_launch_app",
        }
        launched = {
            "success": True,
            "app_name": "Notepad",
            "launched": True,
            "via": "os_startfile",
        }
        with patch.object(wdt, "_run_with_timeout", side_effect=lambda fn, timeout=8.0: miss), patch.object(
            wdt, "windows_launch_app", return_value=launched
        ) as mock_launch:
            r = wdt.windows_focus_app("Notepad", auto_launch=True)
        self.assertTrue(r.get("success"))
        self.assertTrue(r.get("auto_launched_after_focus_miss"))
        mock_launch.assert_called_once_with("Notepad")

    def test_focus_miss_without_can_launch_does_not_auto_launch(self):
        from modules.desktop import windows_desktop_tools as wdt

        reclaim_fail = {
            "success": False,
            "error": "无法捕获应用前台",
            "hwnd": 123,
        }
        with patch.object(
            wdt, "_run_with_timeout", side_effect=lambda fn, timeout=8.0: reclaim_fail
        ), patch.object(wdt, "windows_launch_app") as mock_launch:
            r = wdt.windows_focus_app("记事本", auto_launch=True)
        self.assertFalse(r.get("success"))
        mock_launch.assert_not_called()

    def test_type_content_phrase_redirects_to_type_text(self):
        from modules.desktop.windows_desktop_tools import windows_click_element

        r = windows_click_element('编辑内容为我已经学会写记事本了')
        self.assertFalse(r.get("success"))
        self.assertEqual(r.get("redirect"), "windows_type_text")

    def test_uia_score_rejects_menu_edit_in_long_term(self):
        from modules.desktop.windows_desktop_tools import _uia_score_name_term

        self.assertLess(_uia_score_name_term("编辑", "编辑内容"), 0.5)
        self.assertEqual(_uia_score_name_term("编辑", "编辑"), 1.0)

    def test_noise_gdi_window_filter(self):
        from modules.desktop.windows_desktop_tools import _is_noise_focus_window

        self.assertTrue(_is_noise_focus_window("GDI+ Window", "foo.exe", ""))
        self.assertTrue(_is_noise_focus_window("", "GDI+windows.exe", ""))
        self.assertFalse(_is_noise_focus_window("无标题 - 记事本", "notepad.exe", "Notepad"))


class TestHermesStartUrl(unittest.TestCase):
    def test_resolve_start_url_from_probe(self):
        from modules.ai.ai_chat_tool_loop import ChatToolLoopParams, _resolve_start_url_for_hermes

        params = ChatToolLoopParams(
            message="测一下搜索",
            project_name="p",
            current_plan={"case_url": ""},
            history=[],
            profile=None,
            legacy_model="",
            page_snapshot=None,
            probe_registry=None,
            probe_url="https://example.com/login",
            memory_context=None,
            dom_context_pack=None,
            interaction_context={},
        )
        self.assertEqual(
            _resolve_start_url_for_hermes(params, {}),
            "https://example.com/login",
        )

    def test_resolve_start_url_from_message(self):
        from modules.ai.ai_chat_tool_loop import _resolve_start_url_for_hermes

        self.assertIn(
            "example.com",
            _resolve_start_url_for_hermes(
                None, {"instruction": "打开 https://example.com/a 并登录"}
            ),
        )