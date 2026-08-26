# -*- coding: utf-8 -*-
"""桌面热键投递：组合键必须 SendInput；发送前捕获目标窗前台。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestDesktopHotkeyDelivery(unittest.TestCase):
    def test_press_key_rejects_bare_ctrl(self):
        from modules.desktop.windows_desktop_tools import windows_press_key

        with patch("windows_desktop_tools.get_desktop_target", return_value={"hwnd": 0}):
            r = windows_press_key("ctrl")
        self.assertFalse(r.get("success"))
        self.assertIn("修饰键", r.get("error") or "")

    def test_deliver_keys_reclaims_foreground_then_sends(self):
        from modules.desktop.desktop_input import deliver_keys_to_hwnd

        calls = {"pm": 0, "si": 0, "reclaim": 0}

        def fake_pm(hwnd, vk, *, down=True):
            calls["pm"] += 1
            return True

        def fake_reclaim(hwnd, *, retries=4, steal_click_xy=None):
            calls["reclaim"] += 1
            return {
                "ok": True,
                "hwnd": hwnd,
                "after_fg": hwnd,
                "fg_title": "微信",
            }

        class FakeUser32:
            def SendInput(self, *a, **k):
                calls["si"] += 1
                return 1

        with (
            patch("desktop_input.postmessage_key_to_hwnd", side_effect=fake_pm),
            patch("desktop_input.reclaim_foreground_hwnd", side_effect=fake_reclaim),
            patch("desktop_input.get_foreground_hwnd", return_value=12345),
            patch("desktop_input._user32", return_value=FakeUser32()),
            patch("desktop_input.time.sleep", return_value=None),
        ):
            out = deliver_keys_to_hwnd(12345, ["ctrl", "f"])
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(calls["pm"], 0)
        self.assertGreater(calls["si"], 0)
        self.assertGreaterEqual(calls["reclaim"], 1)
        self.assertIn("sendinput", (out.get("via") or ""))
        self.assertTrue(out.get("fg_captured"))

    def test_deliver_keys_fails_only_after_reclaim_exhausted(self):
        from modules.desktop.desktop_input import deliver_keys_to_hwnd

        with (
            patch(
                "desktop_input.reclaim_foreground_hwnd",
                return_value={
                    "ok": False,
                    "error": "未能把目标窗抢到前台",
                    "hwnd": 12345,
                    "fg_title": "Chrome",
                },
            ),
            patch("desktop_input.time.sleep", return_value=None),
        ):
            out = deliver_keys_to_hwnd(12345, ["ctrl", "f"])
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("via"), "focus_reclaim_failed")
        self.assertTrue(
            "前台" in (out.get("error") or "") or "捕获" in (out.get("suggestion") or ""),
            out,
        )


class TestDesktopFocusCapture(unittest.TestCase):
    def test_score_prefers_weixin_process_over_browser(self):
        from modules.desktop.windows_desktop_tools import _score_focus_candidate

        needles = ["微信", "weixin", "wechat", "weixin.exe"]
        wechat = {
            "title": "微信",
            "process": "Weixin.exe",
            "class_name": "Qt51514QWindowIcon",
            "width": 1000,
            "height": 700,
        }
        chrome = {
            "title": "微信网页版 - Chrome",
            "process": "chrome.exe",
            "class_name": "Chrome_WidgetWin_1",
            "width": 1200,
            "height": 800,
        }
        self.assertGreater(
            _score_focus_candidate(wechat, needles),
            _score_focus_candidate(chrome, needles),
        )

    def test_refresh_rebinds_by_process_when_hwnd_dead(self):
        from modules.desktop.windows_desktop_tools import (
            clear_desktop_target,
            refresh_desktop_target_hwnd,
            set_desktop_target,
        )

        clear_desktop_target()
        set_desktop_target(hwnd=1, label="微信", title="微信", process="Weixin.exe")
        fake_win = {
            "hwnd": 999,
            "title": "微信",
            "process": "Weixin.exe",
            "class_name": "Qt51514QWindowIcon",
            "width": 900,
            "height": 600,
            "visible": True,
            "iconic": False,
        }
        with (
            patch("desktop_input.is_valid_hwnd", return_value=False),
            patch(
                "windows_desktop_tools._enum_focus_candidate_windows",
                return_value=[fake_win],
            ),
        ):
            r = refresh_desktop_target_hwnd()
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("hwnd"), 999)
        self.assertTrue(r.get("refreshed"))


if __name__ == "__main__":
    unittest.main()
