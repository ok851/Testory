# -*- coding: utf-8 -*-
"""观察→动作→核验：UIA 优先于 OCR；禁止 soft_verify 假成功。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestTypeObserveActVerify(unittest.TestCase):
    def test_search_type_fails_when_uia_and_ocr_miss(self):
        from modules.desktop.windows_desktop_tools import (
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
                    "windows_desktop_tools._verify_typed_text",
                    return_value={
                        "ok": False,
                        "error": "已通过 clipboard_ctrl_v 投递，但 UIA/OCR 均未确认",
                        "via": "",
                    },
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
            self.assertTrue(r.get("delivery_ok"), r)
            self.assertIn("投递", str(r.get("error") or "") + str(r.get("suggestion") or ""))
            # 画面变了也不能 soft 成功
            self.assertNotIn("soft_verify", str(r.get("ocr_check") or {}))
        finally:
            clear_desktop_target()

    def test_type_succeeds_when_uia_readback_ok_even_if_ocr_fails(self):
        from modules.desktop.windows_desktop_tools import (
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
                    "windows_desktop_tools._verify_typed_text",
                    return_value={
                        "ok": True,
                        "via": "uia_value",
                        "match": "exact",
                        "token": "舒琪宝宝大王",
                    },
                ),
                patch(
                    "windows_desktop_tools.capture_after_action",
                    return_value={"ok": True, "changed": True},
                ),
                patch("windows_desktop_tools.time.sleep", return_value=None),
            ):
                r = windows_type_text("舒琪宝宝大王")
            self.assertTrue(r.get("success"), r)
            self.assertTrue(r.get("verified"))
            self.assertEqual((r.get("verify") or {}).get("via"), "uia_value")
        finally:
            clear_desktop_target()

    def test_verify_typed_text_prefers_uia_over_ocr(self):
        from modules.desktop.windows_desktop_tools import _verify_typed_text

        with (
            patch(
                "desktop_input.uia_get_focused_edit_text",
                return_value="舒琪宝宝大王",
            ),
            patch(
                "windows_desktop_tools._verify_typed_text_on_screen",
                return_value={"ok": False, "error": "OCR miss"},
            ) as ocr,
        ):
            r = _verify_typed_text("舒琪宝宝大王", hwnd=42, field="search", search_context=True)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("via"), "uia_value")
        ocr.assert_not_called()

    def test_type_succeeds_when_ocr_sees_text(self):
        from modules.desktop.windows_desktop_tools import (
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
                        "ocr_check": {"ok": True, "match": "exact", "via": "ocr_exact"},
                        "verify": {"ok": True, "match": "exact", "via": "ocr_exact"},
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
