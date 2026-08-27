# -*- coding: utf-8 -*-
"""启动智能体时自动启用桌面 MCP / gateway。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestHermesDesktopMcpConfig(unittest.TestCase):
    def test_upsert_config_yaml_idempotent(self):
        from modules.hermes.hermes_desktop_enable import ensure_hermes_config_desktop_control

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch("modules.hermes.hermes_config.hermes_home_dir", return_value=home), patch(
                "modules.hermes.hermes_desktop_enable.hermes_config_yaml_path",
                return_value=home / "config.yaml",
            ):
                r1 = ensure_hermes_config_desktop_control()
                self.assertTrue(r1.get("ok"))
                self.assertTrue(r1.get("changed"))
                cfg_path = home / "config.yaml"
                self.assertTrue(cfg_path.is_file())
                text = cfg_path.read_text(encoding="utf-8")
                self.assertIn("testory-desktop", text)
                self.assertIn("9820", text)

                r2 = ensure_hermes_config_desktop_control()
                self.assertTrue(r2.get("ok"))
                self.assertFalse(r2.get("changed"))

    def test_desktop_hint_mentions_mcp_not_cua_first(self):
        from modules.hermes.hermes_skill_hints import desktop_gateway_auth_hint

        hint = desktop_gateway_auth_hint()
        self.assertIn("9820", hint)
        self.assertIn("MCP", hint)
        self.assertIn("windows_", hint)


class TestThinOuterDesktopPrompt(unittest.TestCase):
    def test_desktop_prompt_mentions_mcp(self):
        from modules.ai.ai_chat_tool_loop import _build_system_prompt

        sp = _build_system_prompt(
            project_name="t",
            current_plan={},
            page_snapshot="",
            dom_pack="",
            memory_context="",
            interaction_note="",
            test_scope="",
            platform_type="desktop",
        )
        self.assertIn("一次", sp)
        self.assertIn("MCP", sp)


if __name__ == "__main__":
    unittest.main()
