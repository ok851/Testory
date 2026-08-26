# -*- coding: utf-8 -*-
"""通用桌面防回退：失败后不得重跑已成功/已尝试步骤；禁止搜索词叠字。"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch


class TestComposePhaseNoSearchReclick(unittest.TestCase):
    def tearDown(self):
        from modules.desktop.windows_desktop_tools import clear_desktop_target

        clear_desktop_target()

    def test_duplicate_search_type_short_circuits(self):
        from modules.desktop.windows_desktop_tools import (
            arm_search_input_focus,
            set_desktop_target,
            windows_type_text,
            _desktop_target,
        )

        set_desktop_target(hwnd=42, label="App")
        arm_search_input_focus(100, 80)
        _desktop_target["last_search_query"] = "早安宝宝大王"
        with (
            patch(
                "windows_desktop_tools.begin_desktop_action_frame",
                return_value={"ok": True, "hwnd": 42, "frame_id": "f1", "before_hash": "h0"},
            ),
            patch(
                "windows_desktop_tools._type_observe_act_verify",
            ) as verify,
        ):
            r = windows_type_text("早安宝宝大王")
        self.assertTrue(r.get("success"), r)
        self.assertTrue(r.get("skipped"), r)
        verify.assert_not_called()


class TestGeneralAntiReplay(unittest.TestCase):
    def test_skip_exact_succeeded_fingerprint(self):
        from modules.ai.ai_chat_tool_loop import (
            _record_succeeded_desktop_action,
            _should_skip_replay_desktop_tool,
        )

        meta: dict = {"succeeded_action_fps": [], "desktop_phase": "start"}
        ok = json.dumps({"success": True})
        _record_succeeded_desktop_action(
            meta, "windows_focus_app", {"app_name": "记事本"}, ok
        )
        skip = _should_skip_replay_desktop_tool(
            "windows_focus_app", {"app_name": "记事本"}, meta
        )
        self.assertIsNotNone(skip)
        self.assertTrue(
            "focus_already_done" in (skip or "")
            or "already_succeeded_no_replay" in (skip or "")
        )

    def test_ocr_fail_still_locks_typed_text(self):
        from modules.ai.ai_chat_tool_loop import (
            _record_succeeded_desktop_action,
            _should_skip_replay_desktop_tool,
        )

        meta: dict = {"succeeded_action_fps": [], "desktop_phase": "search_ready"}
        failed_but_delivered = json.dumps(
            {
                "success": False,
                "delivery": {"ok": True, "via": "clipboard_ctrl_v"},
                "error": "OCR 未看到",
            }
        )
        _record_succeeded_desktop_action(
            meta, "windows_type_text", {"text": "早安宝宝大王", "clear": False}, failed_but_delivered
        )
        self.assertIn("早安宝宝大王", meta.get("typed_texts") or [])
        # clear 不同也不能再 type
        skip = _should_skip_replay_desktop_tool(
            "windows_type_text", {"text": "早安宝宝大王", "clear": True}, meta
        )
        self.assertIsNotNone(skip)
        self.assertTrue(
            "text_already_typed" in (skip or "")
            or "already_succeeded_no_replay" in (skip or "")
            or "search_query_already_typed" in (skip or "")
        )
        # 拼接重复也拦
        skip2 = _should_skip_replay_desktop_tool(
            "windows_type_text", {"text": "早安宝宝大王早安宝宝大王"}, meta
        )
        self.assertIsNotNone(skip2)

    def test_search_family_click_blocked_after_query(self):
        from modules.ai.ai_chat_tool_loop import _should_skip_replay_desktop_tool

        meta = {
            "desktop_phase": "query_typed",
            "search_ui_done": True,
            "auto_typed_search": True,
            "last_search_query": "Alice",
            "typed_texts": ["alice"],
            "succeeded_action_fps": [],
        }
        for desc in ("搜索", "搜索框", "搜索框内容清空", "Search"):
            skip = _should_skip_replay_desktop_tool(
                "windows_click_element", {"description": desc}, meta
            )
            self.assertIsNotNone(skip, desc)

    def test_fail_sets_forward_only(self):
        from modules.ai.ai_chat_tool_loop import _desktop_fail_stop_message, _should_skip_replay_desktop_tool

        meta = {
            "desktop_phase": "query_typed",
            "focused_apps": ["微信"],
            "search_ui_done": True,
            "last_search_query": "Alice",
            "typed_texts": ["alice"],
            "succeeded_action_fps": [],
        }
        msg = _desktop_fail_stop_message("windows_press_key", '{"error":"x"}', meta=meta)
        self.assertTrue(meta.get("repair_forward_only"))
        self.assertIn("已锁定勿回退", msg)
        skip = _should_skip_replay_desktop_tool(
            "windows_focus_app", {"app_name": "微信"}, meta
        )
        self.assertIsNotNone(skip)


if __name__ == "__main__":
    unittest.main()
