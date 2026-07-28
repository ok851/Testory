# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch, MagicMock

from desktop_embed_launch import (
    chromium_embed_flags,
    embed_hooks_enabled,
    merge_embed_args,
    merge_embed_env,
    prepare_embed_launch,
    user_facing_embed_hint,
    webview2_additional_browser_arguments,
)


class TestDesktopEmbedLaunch(unittest.TestCase):
    def test_flags_contain_accessibility_and_port(self):
        flags = chromium_embed_flags(19222)
        self.assertIn("--force-renderer-accessibility", flags)
        self.assertIn("--remote-debugging-port=19222", flags)

    def test_merge_args_appends_once(self):
        with patch.dict(os.environ, {"DESKTOP_EMBED_HOOKS": "1"}, clear=False):
            args, port = merge_embed_args(["--foo"], port=19001)
            self.assertEqual(port, 19001)
            self.assertIn("--foo", args)
            self.assertIn("--force-renderer-accessibility", args)
            args2, _ = merge_embed_args(args, port=19001)
            self.assertEqual(
                args2.count("--force-renderer-accessibility"),
                1,
            )

    def test_merge_env_sets_webview2(self):
        with patch.dict(os.environ, {"DESKTOP_EMBED_HOOKS": "1"}, clear=False):
            env, port = merge_embed_env({}, port=19002)
            self.assertEqual(port, 19002)
            wv = env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") or ""
            self.assertIn("force-renderer-accessibility", wv)
            self.assertIn("19002", wv)

    def test_prepare_embed_launch(self):
        with patch.dict(os.environ, {"DESKTOP_EMBED_HOOKS": "1"}, clear=False):
            prep = prepare_embed_launch(r"C:\App\app.exe", ["--user-data-dir=x"])
            self.assertTrue(prep["hooks_enabled"])
            self.assertTrue(any("remote-debugging-port" in a for a in prep["args"]))
            self.assertIn("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", prep["env"])

    def test_user_hint_no_source_required(self):
        hint = user_facing_embed_hint(hooks_tried=True, cdp_ok=False)
        self.assertIn("点选", hint)
        self.assertNotIn("源码", hint)
        self.assertNotIn("重启", hint)

    @patch.dict(os.environ, {"DESKTOP_EMBED_HOOKS": "1"}, clear=False)
    def test_hooks_can_enable(self):
        self.assertTrue(embed_hooks_enabled())
        args, _ = merge_embed_args([], port=19003)
        self.assertTrue(any("force-renderer-accessibility" in a for a in args))

    @patch.dict(os.environ, {"DESKTOP_EMBED_HOOKS": "0"}, clear=False)
    def test_hooks_default_off_path(self):
        self.assertFalse(embed_hooks_enabled())
        args, _ = merge_embed_args([])
        self.assertEqual(args, [])


if __name__ == "__main__":
    unittest.main()
