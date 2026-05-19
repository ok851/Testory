# -*- coding: utf-8 -*-
import unittest

from packaging.enterprise.update_core import parse_version, version_equals, version_newer


class TestUpdateCore(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(version_newer("1.2.0", "1.1.9"))
        self.assertFalse(version_newer("1.0.0", "1.0.0"))
        self.assertTrue(version_equals("1.1.0", "1.1.0"))

    def test_can_delta_fields(self):
        from packaging.enterprise.update_core import check_for_update
        import os
        from unittest.mock import patch

        manifest = {
            "version": "1.2.0",
            "base_version": "1.1.0",
            "patch_url": "https://example.com/p.bsdiff",
            "patch_sha256": "abc",
            "package_url": "https://example.com/full.exe",
        }
        with patch.dict(os.environ, {"UAT_UPDATE_MANIFEST_URL": "http://x", "UAT_APP_VERSION": "1.1.0"}):
            with patch("packaging.enterprise.update_core.fetch_manifest", return_value=manifest):
                info = check_for_update()
        self.assertIsNotNone(info)
        self.assertTrue(info["can_delta"])


if __name__ == "__main__":
    unittest.main()
