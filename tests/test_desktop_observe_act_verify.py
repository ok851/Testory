# -*- coding: utf-8 -*-
"""观察→动作→核验：禁止 soft_verify 假成功。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestTypeObserveActVerify(unittest.TestCase):
    def test_search_type_requires_ocr_evidence(self):
        from windows_desktop_tools import (
            arm_search_input_focus,
            clear_desktop_target,
            set_desktop_target,
            windows_type_text,
        )

        set_desktop_target(hwnd=42, label="微信", title="微信", process="Weixin.exe")
        arm_search_input_focus(100, 80)
        try:
            with (
                patch(
                    "windows_desktop_tools.begin_desktop_action_frame",
                    return_value={
                        "ok": True,
                        "hwnd": 42,
                        "frame_id": "f-wx",
                        "before_hash": "h0",
                    },
                ),
                patch(
                    "windows_desktop_tools._reclick_armed_search_if_needed",
                    return_value={"ok": True, "x": 100, "y": 84},
                ),
                patch(
                    "windows_desktop_tools._run_one_type_strategy",
                    return_value={"ok": True, "via": "clipboard_ctrl_v", "hwnd": 42},
                ),
                patch(
                    "windows_desktop_tools._verify_typed_text_on_screen",
                    return_value={"ok": False, "error": "OCR 未在画面上看到输入内容"},
                ),
                patch(
                    "windows_desktop_tools.capture_after_action",
                    return_value={"ok": True, "changed": True, "texts_preview": ["搜索网络结果"]},
                ),
                patch("windows_desktop_tools.time.sleep", return_value=None),
            ):
                r = windows_type_text("舒琪宝宝大王")
            self.assertFalse(r.get("success"), r)
            self.assertFalse(r.get("verified"))
            self.assertTrue(r.get("flow_halt"))
            self.assertTrue(r.get("attempts"))
            # 画面变了或出现「网络」也不能 soft 成功
            self.assertNotIn("soft_verify", str(r.get("ocr_check") or {}))
        finally:
            clear_desktop_target()

    def test_type_succeeds_when_ocr_sees_text(self):
        from windows_desktop_tools import (
            clear_desktop_target,
            set_desktop_target,
            windows_type_text,
        )

        set_desktop_target(hwnd=7, label="记事本", title="记事本")
        try:
            with (
                patch(
                    "windows_desktop_tools.begin_desktop_action_frame",
                    return_value={
                        "ok": True,
                        "hwnd": 7,
                        "frame_id": "f-np",
                        "before_hash": "h0",
                    },
                ),
                patch(
                    "windows_desktop_tools._reclick_armed_search_if_needed",
                    return_value={"skipped": True},
                ),
                patch(
                    "windows_desktop_tools._type_observe_act_verify",
                    return_value={
                        "ok": True,
                        "verified": True,
                        "delivery": {"ok": True, "via": "uia_value"},
                        "ocr_check": {"ok": True, "match": "exact"},
                        "capture_after": {"ok": True},
                        "attempts": [{"strategy": "uia", "ocr_ok": True}],
                        "strategy": "uia",
                        "frame_id": "f-np",
                    },
                ),
            ):
                r = windows_type_text("hello")
            self.assertTrue(r.get("success"), r)
            self.assertTrue(r.get("verified"))
            self.assertEqual(r.get("strategy"), "uia")
        finally:
            clear_desktop_target()


if __name__ == "__main__":
    unittest.main()
