"""embedded_browser_service_bootstrap 默认行为。"""

import os
import unittest
from unittest.mock import patch

from embedded_browser_service_bootstrap import embedded_auto_start_gateway


class TestEmbeddedBrowserBootstrap(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "EMBEDDED_BROWSER_GATEWAY_URL": "http://127.0.0.1:8765",
            "EMBEDDED_BROWSER_GATEWAY_SECRET": "s",
            "DEPLOYMENT_PROFILE": "local",
        },
        clear=False,
    )
    def test_auto_start_default_on_local(self):
        os.environ.pop("EMBEDDED_BROWSER_AUTO_START_GATEWAY", None)
        self.assertTrue(embedded_auto_start_gateway())

    @patch.dict(
        os.environ,
        {
            "EMBEDDED_BROWSER_GATEWAY_URL": "http://127.0.0.1:8765",
            "EMBEDDED_BROWSER_GATEWAY_SECRET": "s",
            "EMBEDDED_BROWSER_AUTO_START_GATEWAY": "0",
        },
        clear=False,
    )
    def test_auto_start_respects_off(self):
        self.assertFalse(embedded_auto_start_gateway())


if __name__ == "__main__":
    unittest.main()
