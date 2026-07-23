# -*- coding: utf-8 -*-
"""本地简易注册 / 找回密钥重置 API 流程测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LocalRegisterRecoveryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = str(Path(cls._tmpdir.name) / "test_auth.db")
        os.environ["DATABASE_PATH"] = cls.db_path
        os.environ["UAT_DESKTOP_MODE"] = "1"
        os.environ["DEPLOYMENT_MODE"] = "client"
        os.environ["DESKTOP_LAZY_GATEWAY_BOOT"] = "1"
        os.environ["MOBILE_AUTO_START_GATEWAY"] = "0"
        os.environ["DESKTOP_AUTO_START_GATEWAY"] = "0"
        os.environ["SKIP_ENV_EXAMPLE_SYNC"] = "1"

        # 延迟导入，确保 DATABASE_PATH 已生效
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_normalize_recovery_key(self):
        key = "ABCD-EF01-2345-6789"
        self.assertEqual(self.app_module._normalize_recovery_key(key), key)
        self.assertEqual(self.app_module._normalize_recovery_key("abcdef0123456789"), "ABCD-EF01-2345-6789")

    def test_register_then_recovery_reset(self):
        with patch("app._allow_local_auth", return_value=True), patch(
            "team_server_proxy.should_proxy_path", return_value=False
        ), patch("deployment_hooks.should_proxy_path", return_value=False):
            username = "flow_user_01"
            password = "secret12"
            reg = self.client.post(
                "/api/auth/register/local",
                json={
                    "username": username,
                    "password": password,
                    "confirm_password": password,
                },
            )
            self.assertEqual(reg.status_code, 200, reg.get_data(as_text=True))
            body = reg.get_json()
            self.assertTrue(body.get("success"))
            recovery_key = body.get("recovery_key")
            self.assertTrue(recovery_key and len(recovery_key.split("-")) == 4)

            # 旧密码可登录
            login = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            self.assertEqual(login.status_code, 200, login.get_data(as_text=True))
            self.assertTrue(login.get_json().get("success"))

            new_password = "secret99"
            reset = self.client.post(
                "/api/auth/forgot-password/recovery-reset",
                json={
                    "username": username,
                    "recovery_key": recovery_key.replace("-", ""),  # 无横线也能用
                    "new_password": new_password,
                    "confirm_password": new_password,
                },
            )
            self.assertEqual(reset.status_code, 200, reset.get_data(as_text=True))
            reset_body = reset.get_json()
            self.assertTrue(reset_body.get("success"))
            new_key = reset_body.get("recovery_key")
            self.assertTrue(new_key)
            self.assertNotEqual(new_key, recovery_key)

            # 旧密码失效，新密码可登录
            bad = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            self.assertFalse(bad.get_json().get("success"))

            good = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": new_password},
            )
            self.assertEqual(good.status_code, 200)
            self.assertTrue(good.get_json().get("success"))

            # 旧找回密钥失效
            reuse = self.client.post(
                "/api/auth/forgot-password/recovery-reset",
                json={
                    "username": username,
                    "recovery_key": recovery_key,
                    "new_password": "secret00",
                    "confirm_password": "secret00",
                },
            )
            self.assertEqual(reuse.status_code, 400)

    def test_backend_command_prefers_app_py(self):
        from packaging.uat_desktop import _backend_command

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("# stub\n", encoding="utf-8")
            exe_dir = root / "runtime" / "testory_app"
            exe_dir.mkdir(parents=True)
            (exe_dir / "TestoryBackend.exe").write_bytes(b"MZ")
            py = Path("/fake/python.exe")
            with patch.dict(os.environ, {"TESTORY_PREFER_PROTECTED_BACKEND": ""}, clear=False):
                cmd = _backend_command(root, py)
            self.assertEqual(cmd[-1], str(root / "app.py"))


if __name__ == "__main__":
    unittest.main()
