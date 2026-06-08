"""env_example_sync：本机功能开关提升与解析。"""

import tempfile
import unittest
from pathlib import Path

from env_example_sync import _coerce_local_on_value, _pairs_from_example, sync_env_from_example


class TestEnvExampleSync(unittest.TestCase):
    def test_coerce_local_on_value(self):
        self.assertEqual(_coerce_local_on_value("ENABLE_MOBILE", "0"), "1")
        self.assertEqual(_coerce_local_on_value("ENABLE_MOBILE", "1"), "1")
        self.assertEqual(_coerce_local_on_value("HERMES_GATEWAY_URL", "http://x"), "http://x")

    def test_pairs_from_commented_example(self):
        text = "# ENABLE_MOBILE=0\n# EMBEDDED_BROWSER_AUTO_START_GATEWAY=0\n"
        pairs = dict(_pairs_from_example(text))
        self.assertEqual(pairs["ENABLE_MOBILE"], "1")
        self.assertEqual(pairs["EMBEDDED_BROWSER_AUTO_START_GATEWAY"], "1")

    def test_sync_appends_missing_with_on_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env.example").write_text(
                "# AI_LOCATOR_RESOLVE_ENABLE=0\nLOCAL_MEMORY_ENABLE=0\n",
                encoding="utf-8",
            )
            (root / ".env").write_text("DEPLOYMENT_PROFILE=local\n", encoding="utf-8")
            r = sync_env_from_example(root, ignore_skip=True)
            self.assertTrue(r["ok"])
            self.assertIn("AI_LOCATOR_RESOLVE_ENABLE", r["added"])
            env = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("AI_LOCATOR_RESOLVE_ENABLE=1", env)
            self.assertIn("LOCAL_MEMORY_ENABLE=1", env)


if __name__ == "__main__":
    unittest.main()
