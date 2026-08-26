# -*- coding: utf-8 -*-
import json
import os
import unittest

from modules.desktop.desktop_env_config import prepare_desktop_step, resolve_path_or_alias


class TestDesktopEnvConfig(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_resolve_alias(self):
        os.environ["DESKTOP_APP_ALIASES"] = '{"erp":"C:\\\\ERP\\\\a.exe"}'
        self.assertEqual(resolve_path_or_alias("erp"), "C:\\ERP\\a.exe")
        self.assertEqual(resolve_path_or_alias("@erp"), "C:\\ERP\\a.exe")

    def test_prepare_launch_from_default(self):
        os.environ["DESKTOP_DEFAULT_LAUNCH_PATH"] = "C:\\Apps\\client.exe"
        s = prepare_desktop_step({
            "action": "launch_app",
            "automation_layer": "desktop",
            "input_value": "",
        })
        self.assertEqual(s["desktop_spec"]["path"], "C:\\Apps\\client.exe")

    def test_prepare_launch_from_alias_default(self):
        os.environ["DESKTOP_APP_ALIASES"] = '{"default":"notepad.exe"}'
        os.environ.pop("DESKTOP_DEFAULT_LAUNCH_PATH", None)
        s = prepare_desktop_step({"action": "launch_app", "input_value": "default"})
        path = s["desktop_spec"]["path"]
        self.assertTrue(
            path == "notepad.exe" or path.lower().endswith("notepad.exe"),
            path,
        )

    def test_empty_launch_does_not_auto_notepad(self):
        os.environ["DESKTOP_APP_ALIASES"] = '{"default":"notepad.exe"}'
        os.environ.pop("DESKTOP_DEFAULT_LAUNCH_PATH", None)
        os.environ.pop("DESKTOP_LAUNCH_FALLBACK", None)
        s = prepare_desktop_step({"action": "launch_app", "input_value": ""})
        self.assertNotIn("path", s.get("desktop_spec") or {})

    def test_prepare_launch_from_alias_object_with_args(self):
        os.environ["DESKTOP_APP_ALIASES"] = json.dumps(
            {
                "erp": {
                    "path": r"C:\Python\python.exe",
                    "args": [r"D:\fake_erp.py", "--order-id", "{order_id}"],
                    "window_title_re": "(?i)^{order_id}$",
                }
            }
        )
        # 步骤侧不带 vars；对象 args 原样写入（token 由计划构建时替换）
        s = prepare_desktop_step({"action": "launch_app", "input_value": "@erp"})
        spec = s.get("desktop_spec") or {}
        self.assertEqual(spec.get("path"), r"C:\Python\python.exe")
        self.assertEqual(spec.get("args")[0], r"D:\fake_erp.py")
        self.assertEqual(spec.get("alias"), "erp")

    def test_resolve_launch_spec_substitutes_order_id(self):
        from modules.desktop.desktop_env_config import resolve_launch_spec

        os.environ["DESKTOP_APP_ALIASES"] = json.dumps(
            {
                "erp": {
                    "path": "py.exe",
                    "args": ["app.py", "--order-id", "{order_id}"],
                }
            }
        )
        launch = resolve_launch_spec("@erp", variables={"order_id": "ORD-9"})
        self.assertEqual(launch["args"][-1], "ORD-9")

    def test_launch_uses_selector_as_program_name(self):
        os.environ.pop("DESKTOP_APP_ALIASES", None)
        s = prepare_desktop_step({
            "action": "launch_app",
            "input_value": "",
            "selector_value": "calc.exe",
        })
        self.assertIn("calc", (s.get("desktop_spec") or {}).get("path", "").lower())

    def test_attach_merge_title_only_when_flag_set(self):
        os.environ["DESKTOP_DEFAULT_ATTACH_TITLE_RE"] = ".*Notepad.*"
        os.environ["DESKTOP_AUTO_ATTACH_DEFAULT"] = "0"
        s = prepare_desktop_step({"action": "attach_window", "input_value": ""})
        spec = s.get("desktop_spec") or {}
        self.assertNotIn("window_title_re", spec)
        os.environ["DESKTOP_AUTO_ATTACH_DEFAULT"] = "1"
        s2 = prepare_desktop_step({"action": "attach_window", "input_value": ""})
        self.assertIn("window_title_re", s2.get("desktop_spec") or {})


if __name__ == "__main__":
    unittest.main()
