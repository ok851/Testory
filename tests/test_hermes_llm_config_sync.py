# -*- coding: utf-8 -*-
"""平台前端切换模型 → Hermes config.yaml model 同步。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestHermesLlmConfigSync(unittest.TestCase):
    def test_normalize_base_url_adds_v1(self):
        from modules.hermes.hermes_config import _normalize_openai_compatible_base_url

        self.assertEqual(
            _normalize_openai_compatible_base_url("https://api.deepseek.com"),
            "https://api.deepseek.com/v1",
        )
        self.assertEqual(
            _normalize_openai_compatible_base_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/v1",
        )

    def test_sync_writes_config_yaml_custom_provider(self):
        from modules.hermes.hermes_config import (
            hermes_env_llm_snapshot,
            sync_platform_llm_to_hermes_config_yaml,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch("hermes_config.hermes_home_dir", return_value=home):
                info = sync_platform_llm_to_hermes_config_yaml(
                    base_url="https://api.deepseek.com",
                    model_id="deepseek-v4-pro",
                    api_key="sk-test-key",
                    api_style="openai_compatible",
                )
                self.assertTrue(info.get("changed"))
                cfg_path = home / "config.yaml"
                self.assertTrue(cfg_path.is_file())
                import yaml

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                model = cfg["model"]
                self.assertEqual(model["provider"], "custom")
                self.assertEqual(model["default"], "deepseek-v4-pro")
                self.assertEqual(model["base_url"], "https://api.deepseek.com/v1")
                self.assertEqual(model["api_key"], "sk-test-key")

                snap = hermes_env_llm_snapshot()
                self.assertEqual(snap["model"], "deepseek-v4-pro")
                self.assertEqual(snap["provider"], "custom")
                self.assertIn("deepseek.com", snap["base_url"])

                # idempotent
                info2 = sync_platform_llm_to_hermes_config_yaml(
                    base_url="https://api.deepseek.com",
                    model_id="deepseek-v4-pro",
                    api_key="sk-test-key",
                    api_style="openai_compatible",
                )
                self.assertFalse(info2.get("changed"))

    def test_sync_env_also_sets_inference_provider(self):
        from modules.hermes.hermes_config import sync_platform_llm_credentials_to_hermes_env

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            registry = {
                "active_profile_id": "p1",
                "profiles": [
                    {
                        "id": "p1",
                        "provider": "deepseek",
                        "api_style": "openai_compatible",
                        "base_url": "https://api.deepseek.com",
                        "model_id": "deepseek-v4-pro",
                        "api_key": "sk-live-test",
                        "label": "DeepSeek",
                    }
                ],
            }

            def _fake_status():
                return {
                    "ok": True,
                    "hermes_profile": registry["profiles"][0],
                    "reason": "active",
                }

            with (
                patch("hermes_config.hermes_home_dir", return_value=home),
                patch("hermes_config.hermes_upstream_llm_status", _fake_status),
                patch("hermes_config._read_active_llm_profile", return_value=registry["profiles"][0]),
            ):
                out = sync_platform_llm_credentials_to_hermes_env()
                self.assertTrue(out.get("synced"))
                self.assertTrue(out.get("config_changed"))
                env_text = (home / ".env").read_text(encoding="utf-8")
                self.assertIn("HERMES_INFERENCE_PROVIDER=custom", env_text)
                self.assertIn("CUSTOM_BASE_URL=https://api.deepseek.com/v1", env_text)
                self.assertIn("DEEPSEEK_API_KEY=sk-live-test", env_text)
                import yaml

                model = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))["model"]
                self.assertEqual(model["provider"], "custom")
                self.assertEqual(model["default"], "deepseek-v4-pro")

    def test_xiaomi_active_does_not_fallback_to_deepseek(self):
        """前端选 MiMo 时，Hermes 必须同步 MiMo，禁止静默改写为 DeepSeek。"""
        from modules.hermes.hermes_config import hermes_upstream_llm_status, sync_platform_llm_credentials_to_hermes_env

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            mimo = {
                "id": "mimo1",
                "provider": "custom_openai",
                "api_style": "openai_compatible",
                "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                "model_id": "mimo-v2.5-pro",
                "api_key": "tp-test-key-xxxxxxxx",
                "label": "mimo-v2.5-pro",
            }
            deepseek = {
                "id": "ds1",
                "provider": "deepseek",
                "api_style": "openai_compatible",
                "base_url": "https://api.deepseek.com",
                "model_id": "deepseek-v4-pro",
                "api_key": "sk-deepseek-should-not-use",
                "label": "deepseek",
            }
            registry = {
                "active_profile_id": "mimo1",
                "profiles": [deepseek, mimo],
            }
            reg_path = home / "ai_model_registry.json"
            reg_path.write_text(json.dumps(registry), encoding="utf-8")

            with (
                patch("hermes_config.hermes_home_dir", return_value=home),
                patch("hermes_config._read_active_llm_profile", return_value=mimo),
                patch("ai_config_paths.ai_model_registry_path", return_value=reg_path),
            ):
                st = hermes_upstream_llm_status()
                self.assertTrue(st.get("ok"))
                self.assertNotEqual(st.get("reason"), "fallback_bearer_profile")
                self.assertEqual((st.get("hermes_profile") or {}).get("model_id"), "mimo-v2.5-pro")

                out = sync_platform_llm_credentials_to_hermes_env()
                self.assertEqual(out.get("synced_model"), "mimo-v2.5-pro")
                import yaml

                model = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))["model"]
                self.assertEqual(model["default"], "mimo-v2.5-pro")
                self.assertIn("xiaomimimo.com", model["base_url"])
                self.assertNotIn("deepseek", (model.get("default") or "").lower())


if __name__ == "__main__":
    unittest.main()
