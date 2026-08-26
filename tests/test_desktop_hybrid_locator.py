# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import MagicMock, patch

from modules.desktop.desktop_visual_engine import VisualStepPayload
from modules.desktop.desktop_hybrid_locator import _uia_click_from_center


class TestDesktopHybridLocator(unittest.TestCase):
    def test_uia_click_from_center(self):
        x, y = _uia_click_from_center(100, 200, 10, 5, 40, 40)
        self.assertEqual(x, 100 - 20 + 10)
        self.assertEqual(y, 200 - 20 + 5)

    @patch("desktop_shell_application.try_resolve_shell_application_step", return_value=None)
    @patch("desktop_hybrid_locator.resolve_visual_click_point")
    @patch("desktop_uia_snapshot.resolve_uia_click_point")
    def test_resolve_uia_first(self, mock_uia, mock_vis, _com):
        from modules.desktop.desktop_hybrid_locator import resolve_desktop_click_point

        mock_uia.return_value = MagicMock(
            ok=True, x=50, y=60, score=1.0, anchor=(50, 60)
        )
        step = {
            "automation_layer": "desktop",
            "selector_type": "visual",
            "action": "click",
            "selector_value": VisualStepPayload(
                template_image_base64="abc",
                click_offset_x=20,
                click_offset_y=20,
                match_threshold=0.72,
                match_method="auto",
                template_width=40,
                template_height=40,
                search_anchor_x=1,
                search_anchor_y=2,
                element_snapshot={"selector": {"key_candidates": [{"property": "uia-name", "value": "x"}]}},
            ).to_json(),
        }
        r = resolve_desktop_click_point(step)
        self.assertEqual(r.resolved_via, "uia")
        mock_vis.assert_not_called()

    @patch("desktop_shell_application.try_resolve_shell_application_step", return_value=None)
    @patch("desktop_hybrid_locator.resolve_visual_click_point")
    @patch("desktop_uia_snapshot.resolve_uia_click_point")
    def test_resolve_visual_fallback(self, mock_uia, mock_vis, _com):
        from modules.desktop.desktop_uia_snapshot import UiaResolveResult
        from modules.desktop.desktop_hybrid_locator import resolve_desktop_click_point

        mock_uia.return_value = UiaResolveResult(ok=False, error_code="timeout")
        mock_vis.return_value = (10, 20, 0.88)
        step = {
            "automation_layer": "desktop",
            "selector_type": "visual",
            "action": "click",
            "selector_value": VisualStepPayload(
                template_image_base64="abc",
                click_offset_x=0,
                click_offset_y=0,
                match_threshold=0.72,
                match_method="auto",
                template_width=10,
                template_height=10,
                search_anchor_x=100,
                search_anchor_y=200,
                element_snapshot={"selector": {}},
            ).to_json(),
        }
        r = resolve_desktop_click_point(step)
        self.assertEqual(r.resolved_via, "visual_roi")
        self.assertEqual(r.x, 10)

    @patch("desktop_shell_application.try_resolve_shell_application_step", return_value=None)
    @patch("desktop_shell_listview.try_resolve_shell_listview_step", return_value=None)
    @patch("desktop_hybrid_locator.resolve_visual_click_point")
    @patch("desktop_uia_snapshot.resolve_uia_click_point")
    def test_resolve_uia_from_locator_candidates(self, mock_uia, mock_vis, _shell, _com):
        from modules.desktop.desktop_hybrid_locator import resolve_desktop_click_point
        from modules.desktop.desktop_shell_listview import ShellIconTarget

        mock_uia.return_value = MagicMock(
            ok=True, x=80, y=90, score=1.0, anchor=(80, 90)
        )
        with patch(
            "desktop_hybrid_locator._try_shell_at_screen_for_listitem",
            return_value=ShellIconTarget(
                listview_hwnd=1,
                index=-1,
                icon_name="控制面板",
                client_x=80,
                client_y=90,
                screen_x=80,
                screen_y=90,
            ),
        ):
            uia_nodes = [
                {"control_type": "Pane", "class_name": "#32769", "name": "桌面 1"},
                {
                    "control_type": "ListItem",
                    "class_name": "",
                    "name": "控制面板",
                    "automation_id": "",
                },
            ]
            step = {
                "automation_layer": "desktop",
                "selector_type": "visual",
                "action": "click",
                "selector_value": VisualStepPayload(
                    template_image_base64="abc",
                    click_offset_x=0,
                    click_offset_y=0,
                    match_threshold=0.72,
                    match_method="auto",
                    template_width=40,
                    template_height=40,
                ).to_json(),
                "locator_candidates": json.dumps(
                    [
                        {
                            "selector_type": "uia_path",
                            "selector_value": json.dumps(uia_nodes, ensure_ascii=False),
                            "score": 98,
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
            r = resolve_desktop_click_point(step)
            self.assertEqual(r.resolved_via, "shell_listview")
            mock_vis.assert_not_called()

    @patch("desktop_shell_listview.try_resolve_shell_listview_step", return_value=None)
    @patch("desktop_hybrid_locator.resolve_visual_click_point")
    @patch("desktop_uia_snapshot.resolve_uia_click_point")
    def test_resolve_shell_com_first(self, mock_uia, mock_vis, _shell):
        from modules.desktop.desktop_hybrid_locator import resolve_desktop_click_point
        from modules.desktop.desktop_shell_application import ShellComTarget

        com = ShellComTarget(icon_name="控制面板", matched_name="控制面板")
        with patch(
            "desktop_shell_application.try_resolve_shell_application_step",
            return_value=com,
        ):
            uia_nodes = [
                {
                    "control_type": "ListItem",
                    "class_name": "",
                    "name": "控制面板",
                    "automation_id": "",
                },
            ]
            step = {
                "automation_layer": "desktop",
                "selector_type": "visual",
                "action": "double_click",
                "selector_value": VisualStepPayload(
                    template_image_base64="abc",
                    click_offset_x=0,
                    click_offset_y=0,
                    match_threshold=0.72,
                    match_method="auto",
                    template_width=40,
                    template_height=40,
                ).to_json(),
                "locator_candidates": json.dumps(
                    [
                        {
                            "selector_type": "uia_path",
                            "selector_value": json.dumps(uia_nodes, ensure_ascii=False),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
            r = resolve_desktop_click_point(step)
        self.assertEqual(r.resolved_via, "shell_com")
        mock_uia.assert_not_called()
        mock_vis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
