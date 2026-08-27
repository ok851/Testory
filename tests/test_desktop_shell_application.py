# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from modules.desktop.desktop_shell_application import (
    ShellComTarget,
    _match_shell_item_name,
    execute_shell_application_action,
    find_desktop_item_by_name,
    resolve_shell_application_icon,
    shell_com_enabled,
)


class TestDesktopShellApplication(unittest.TestCase):
    def test_match_shell_item_name(self):
        self.assertTrue(_match_shell_item_name("控制面板", "控制面板"))
        self.assertTrue(
            _match_shell_item_name(
                "控制面板.{26EE0668-A00A-44D7-9371-BEEB064C45683}",
                "控制面板",
            )
        )

    def test_shell_com_enabled_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(shell_com_enabled())

    @patch("modules.desktop.desktop_shell_application._get_desktop_namespace")
    def test_find_desktop_item(self, mock_ns):
        item = MagicMock()
        item.Name = "控制面板"
        ns = MagicMock()
        ns.Items.return_value = [item]
        mock_ns.return_value = ns
        found = find_desktop_item_by_name("控制面板")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "控制面板")

    @patch("modules.desktop.desktop_shell_application.find_desktop_item_by_name")
    def test_resolve_icon(self, mock_find):
        mock_find.return_value = ("控制面板", MagicMock())
        target = resolve_shell_application_icon("控制面板")
        self.assertIsInstance(target, ShellComTarget)
        self.assertEqual(target.matched_name, "控制面板")

    @patch("modules.desktop.desktop_shell_application.invoke_desktop_item")
    @patch("modules.desktop.desktop_shell_application.find_desktop_item_by_name")
    def test_execute_action(self, mock_find, mock_invoke):
        mock_item = MagicMock()
        mock_find.return_value = ("控制面板", mock_item)
        step = {"description": "双击「控制面板」"}
        with patch(
            "modules.desktop.desktop_shell_listview.icon_name_from_step",
            return_value="控制面板",
        ):
            out = execute_shell_application_action(step, "double_click")
        self.assertEqual(out.matched_name, "控制面板")
        mock_invoke.assert_called_once_with(mock_item, "double_click")


if __name__ == "__main__":
    unittest.main()
