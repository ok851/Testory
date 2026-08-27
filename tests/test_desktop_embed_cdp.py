# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock

from modules.desktop.desktop_embed_cdp import (
    _build_selector_from_dom,
    _first_css_candidate,
    _is_embed_host_class,
    _pick_page_ws,
)


class TestDesktopEmbedCdp(unittest.TestCase):
    def test_is_embed_host_class(self):
        self.assertTrue(_is_embed_host_class("Chrome_RenderWidgetHostHWND"))
        self.assertTrue(_is_embed_host_class("CefBrowserWindow"))
        self.assertTrue(_is_embed_host_class("Chrome_WidgetWin_1"))
        self.assertFalse(_is_embed_host_class("Button"))

    def test_build_selector_prefers_testid_and_id(self):
        sel = _build_selector_from_dom(
            {
                "tag": "button",
                "id": "loginBtn",
                "name": "",
                "className": "primary btn",
                "text": "登录",
                "ariaLabel": "",
                "role": "button",
                "testId": "login",
            }
        )
        self.assertEqual(sel["resolved_via"], "embed_cdp")
        props = [c["property"] for c in sel["key_candidates"]]
        self.assertIn("css", props)
        self.assertTrue(any("data-testid" in (c.get("value") or "") for c in sel["key_candidates"]))
        self.assertTrue(any(c.get("value") == "#loginBtn" for c in sel["key_candidates"]))

    def test_first_css_candidate(self):
        css = _first_css_candidate(
            {
                "key_candidates": [
                    {"property": "dom-text", "value": "登录"},
                    {"property": "css", "value": "#ok"},
                ]
            }
        )
        self.assertEqual(css, "#ok")

    def test_pick_page_ws_prefers_page(self):
        ws = _pick_page_ws(
            [
                {"type": "iframe", "webSocketDebuggerUrl": "ws://i"},
                {"type": "page", "webSocketDebuggerUrl": "ws://p"},
                {"type": "service_worker", "webSocketDebuggerUrl": "ws://s"},
            ]
        )
        self.assertEqual(ws, "ws://p")

    @patch("modules.desktop.desktop_embed_cdp.probe_cdp_port", return_value=None)
    @patch("modules.desktop.desktop_embed_cdp.discover_listening_ports_for_pid", return_value=[])
    @patch("modules.desktop.desktop_embed_cdp._get_pid_from_hwnd", return_value=1234)
    def test_capture_returns_none_without_cdp(self, *_mocks):
        from modules.desktop.desktop_embed_cdp import capture_embed_element_at_point

        with patch(
            "modules.desktop.desktop_win32_snapshot.window_from_point", return_value=1
        ), patch(
            "modules.desktop.desktop_win32_snapshot.get_window_class_name",
            return_value="Chrome_RenderWidgetHostHWND",
        ), patch(
            "modules.desktop.desktop_win32_snapshot.get_window_rect",
            return_value=(0, 0, 800, 600),
        ), patch(
            "modules.desktop.desktop_win32_snapshot.get_process_name_from_hwnd",
            return_value="myapp.exe",
        ), patch(
            "modules.desktop.desktop_win32_snapshot.get_top_level_window",
            return_value=1,
        ):
            self.assertIsNone(capture_embed_element_at_point(100, 100))


if __name__ == "__main__":
    unittest.main()
