# -*- coding: utf-8 -*-
"""桌面网关密钥自动对齐：用户无需关心环境变量。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestDesktopSecretResolve(unittest.TestCase):
    def test_adopts_secret_that_passes_probe(self):
        from modules.desktop.desktop_service_bootstrap import resolve_desktop_gateway_secret

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".env").write_text(
                "DESKTOP_AGENT_GATEWAY_SECRET=secret-from-file\n",
                encoding="utf-8",
            )

            def _probe(secret: str, *, host: str, port: int) -> bool:
                return secret == "secret-from-file"

            with (
                patch("modules.desktop.desktop_service_bootstrap._ROOT", home),
                patch("modules.desktop.desktop_service_bootstrap._port_listening", return_value=True),
                patch("modules.desktop.desktop_service_bootstrap._probe_desktop_secret", side_effect=_probe),
                patch("modules.desktop.desktop_service_bootstrap._persist_desktop_secret_to_hermes"),
                patch.dict("os.environ", {"DESKTOP_AGENT_GATEWAY_SECRET": "wrong-default"}, clear=False),
            ):
                # also hide hermes home lookup noise
                with patch(
                    "modules.desktop.desktop_service_bootstrap._dotenv_secret_candidates",
                    return_value=["secret-from-file"],
                ):
                    got = resolve_desktop_gateway_secret(persist_to_hermes=False)
                self.assertEqual(got, "secret-from-file")


if __name__ == "__main__":
    unittest.main()
