# -*- coding: utf-8 -*-
import unittest

from modules.desktop.desktop_uia_snapshot import (
    SnapshotCaptureResult,
    _class_matches,
    _is_volatile_name,
    _merge_redundant_panes,
    _normalize_class_for_chain,
    sanitize_selector,
)


class TestDesktopUiaSnapshot(unittest.TestCase):
    def test_normalize_workerw_progman(self):
        self.assertEqual(
            _normalize_class_for_chain("WorkerW"),
            "regex:(WorkerW|Progman)",
        )
        self.assertEqual(
            _normalize_class_for_chain("Progman"),
            "regex:(WorkerW|Progman)",
        )
        self.assertEqual(_normalize_class_for_chain("SysListView32"), "SysListView32")

    def test_volatile_name(self):
        self.assertTrue(_is_volatile_name("修改日期: 2024/5/15"))
        self.assertFalse(_is_volatile_name("控制面板"))

    def test_class_regex_match(self):
        self.assertTrue(_class_matches("regex:(WorkerW|Progman)", "WorkerW"))
        self.assertTrue(_class_matches("regex:(WorkerW|Progman)", "Progman"))
        self.assertFalse(_class_matches("regex:(WorkerW|Progman)", "Notepad"))

    def test_merge_redundant_panes(self):
        chain = [
            {"control_type": "Pane", "class_name": "A"},
            {"control_type": "Pane"},
            {"control_type": "List", "class_name": "SysListView32"},
        ]
        out = _merge_redundant_panes(chain)
        self.assertEqual(len(out), 2)

    def test_sanitize_selector_skips_volatile(self):
        class _El:
            def friendly_class_name(self):
                return "ListItem"

            def class_name(self):
                return ""

            def window_text(self):
                return "BM文件.zip"

            def automation_id(self):
                return ""

        sel = sanitize_selector(_El(), [])
        self.assertEqual(sel["anchor_props"], "ListItem")
        self.assertTrue(sel["key_candidates"])
        self.assertEqual(sel["key_candidates"][0]["value"], "BM文件.zip")

    def test_capture_result_dataclass(self):
        r = SnapshotCaptureResult(ok=False, error_code="timeout")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "timeout")

    def test_desktop_root_name(self):
        from modules.desktop.desktop_uia_snapshot import _is_desktop_root_name

        self.assertTrue(_is_desktop_root_name("桌面"))
        self.assertTrue(_is_desktop_root_name("Desktop"))
        self.assertTrue(_is_desktop_root_name("桌面 1"))
        self.assertFalse(_is_desktop_root_name("记事本"))

    def test_fake_container_patterns_aligned(self):
        from modules.desktop.desktop_visual_picker import _is_fake_container

        self.assertTrue(_is_fake_container("Chrome_RenderWidgetHostHWND", ""))
        self.assertTrue(_is_fake_container("", "CefBrowserWindow"))
        self.assertTrue(_is_fake_container("", "Electron"))
        self.assertFalse(_is_fake_container("确定", "Button"))


if __name__ == "__main__":
    unittest.main()
