# -*- coding: utf-8 -*-
"""回归：桌面流程闸 + Ctrl+F 解析 + 搜索候选挑选。"""
from __future__ import annotations

import json
import unittest


class TestParseKeyCombo(unittest.TestCase):
    def test_ctrl_f_splits(self):
        from windows_desktop_tools import _parse_key_combo

        self.assertEqual(_parse_key_combo("Ctrl+F"), ["ctrl", "f"])
        self.assertEqual(_parse_key_combo("ctrl+shift+esc"), ["ctrl", "shift", "esc"])

    def test_pywinauto_caret_f(self):
        from windows_desktop_tools import _parse_key_combo

        self.assertEqual(_parse_key_combo("^f"), ["^f"])


class TestPickSearchCandidate(unittest.TestCase):
    def test_prefers_exact_search(self):
        from windows_desktop_tools import _pick_search_uia_candidate

        cands = [
            {"name": "标签页搜索", "score": 0.98, "x": 1, "y": 1},
            {"name": "用户搜索：", "score": 0.98, "x": 2, "y": 2},
            {"name": "搜索", "score": 0.9, "x": 3, "y": 3},
        ]
        picked = _pick_search_uia_candidate(cands)
        self.assertEqual(picked.get("name"), "搜索")

    def test_falls_back_to_user_search(self):
        from windows_desktop_tools import _pick_search_uia_candidate

        cands = [
            {"name": "标签页搜索", "score": 0.99},
            {"name": "用户搜索：", "score": 0.98},
        ]
        picked = _pick_search_uia_candidate(cands)
        self.assertEqual(picked.get("name"), "用户搜索：")


class TestDesktopToolFailed(unittest.TestCase):
    def test_success_false(self):
        from ai_chat_tool_loop import _desktop_tool_failed

        self.assertTrue(_desktop_tool_failed(json.dumps({"success": False, "error": "x"})))
        self.assertFalse(_desktop_tool_failed(json.dumps({"success": True})))
        self.assertTrue(_desktop_tool_failed(json.dumps({"flow_halt": True})))


class TestPreferOuterDesktopTools(unittest.TestCase):
    def test_export(self):
        from ai_chat_tool_loop import prefer_outer_desktop_tools

        self.assertTrue(prefer_outer_desktop_tools(platform_type="desktop"))


class TestSearchArmFocus(unittest.TestCase):
    def test_arm_and_reclick_coords(self):
        from windows_desktop_tools import (
            arm_search_input_focus,
            clear_search_input_focus,
            get_desktop_target,
            _reclick_armed_search_if_needed,
        )
        from unittest.mock import patch

        clear_search_input_focus()
        arm_search_input_focus(100, 200)
        tgt = get_desktop_target()
        self.assertEqual(tgt.get("search_xy"), (100, 200))
        with patch("desktop_input.screen_click") as sc, patch(
            "desktop_input.force_focus_hwnd", return_value=True
        ), patch("windows_desktop_tools.time.sleep"):
            r = _reclick_armed_search_if_needed()
            self.assertTrue(r.get("ok"))
            sc.assert_called_once_with(100, 204)
        clear_search_input_focus()


class TestOcrFuzzySearch(unittest.TestCase):
    def test_matches_garbled_search(self):
        from windows_desktop_tools import _ocr_text_matches_term

        self.assertTrue(_ocr_text_matches_term("搜索", "搜索"))
        self.assertTrue(_ocr_text_matches_term("搜素", "搜索"))
        self.assertTrue(_ocr_text_matches_term("Search", "搜索框"))
        self.assertFalse(_ocr_text_matches_term("商城开发小组", "搜索"))


class TestGeometryWechatSearch(unittest.TestCase):
    def test_geometry_requires_wechat_title(self):
        from windows_desktop_tools import (
            _geometry_wechat_search_target,
            clear_desktop_target,
            set_desktop_target,
        )

        clear_desktop_target()
        set_desktop_target(hwnd=1, label="记事本", title="无标题 - 记事本")
        self.assertIsNone(_geometry_wechat_search_target(1))

    def test_geometry_point_inside_window(self):
        from unittest.mock import patch
        import ctypes
        from windows_desktop_tools import (
            _geometry_wechat_search_target,
            clear_desktop_target,
            set_desktop_target,
        )

        clear_desktop_target()
        set_desktop_target(hwnd=42, label="微信", title="微信", process="WeChat.exe")

        def fake_get(hwnd, rect_ref):
            rect = rect_ref._obj
            rect.left = 100
            rect.top = 100
            rect.right = 900
            rect.bottom = 700
            return 1

        with patch("windows_desktop_tools._hwnd_title", return_value="微信"), patch.object(
            ctypes.windll.user32, "GetWindowRect", side_effect=fake_get
        ):
            geo = _geometry_wechat_search_target(42)
        self.assertIsNotNone(geo)
        self.assertEqual(geo.get("via"), "geometry_wechat_search")
        self.assertTrue(100 < geo["x"] < 900)
        self.assertTrue(100 < geo["y"] < 700)
        clear_desktop_target()


class TestPendingContactParse(unittest.TestCase):
    def test_parse_wechat_send_message(self):
        from ai_chat_tool_loop import _pending_contact_from_user_message

        c = _pending_contact_from_user_message(
            '帮我打开电脑中已经在运行的微信，并给“舒琪宝宝大王”发送消息“你好，我是AI”'
        )
        self.assertEqual(c, "舒琪宝宝大王")


if __name__ == "__main__":
    unittest.main()
