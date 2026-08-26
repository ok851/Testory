# -*- coding: utf-8 -*-
"""剪贴板粘贴：Win64 GlobalAlloc/GlobalLock restype 回归。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestClipboardPasteWin64(unittest.TestCase):
    def test_paste_does_not_av_on_chinese(self):
        """写剪贴板 + 假 SendInput，确认 64 位不再 access violation。"""
        from modules.desktop.desktop_input import _paste_unicode_via_clipboard

        with patch("ctypes.windll.user32.SendInput", return_value=1):
            try:
                _paste_unicode_via_clipboard("舒琪宝宝大王")
            except OSError as e:
                self.fail(f"Win64 clipboard AV or OSError: {e}")
            except Exception as e:
                # OpenClipboard 偶发被占可接受；但禁止 access violation
                if "access violation" in str(e).lower() or "0x00000000" in str(e):
                    self.fail(f"clipboard path still AV: {e}")


if __name__ == "__main__":
    unittest.main()
