# -*- coding: utf-8 -*-
import unittest

from desktop_locator import _normalize_best_match


class TestDesktopLocatorBestMatch(unittest.TestCase):
    def test_false_means_exact_match(self):
        for raw in (False, 0, "0", "false", "no", "off", None):
            self.assertIsNone(_normalize_best_match({"best_match": raw}))

    def test_true_enables_fuzzy(self):
        for raw in (True, 1, "1", "true", "yes", "on"):
            self.assertTrue(_normalize_best_match({"best_match": raw}))

    def test_string_passthrough(self):
        self.assertEqual(
            _normalize_best_match({"best_match": "5.小兰泊车商城平台使用说明书.docx"}),
            "5.小兰泊车商城平台使用说明书.docx",
        )


if __name__ == "__main__":
    unittest.main()
