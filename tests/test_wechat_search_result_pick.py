# -*- coding: utf-8 -*-
"""微信搜索结果：同名多候选时优先联系人首条。"""
from __future__ import annotations

import unittest


class TestWechatSearchResultPick(unittest.TestCase):
    def test_prefers_contact_over_web_and_search_box(self):
        from windows_desktop_tools import _pick_wechat_search_result_candidate

        query = "舒琪宝宝大王"
        # 搜索框原文（偏上）+ 联系人行 + 网络结果行
        cands = [
            {"name": query, "x": 120, "y": 48, "score": 0.9, "via": "ocr"},
            {"name": query, "x": 140, "y": 160, "score": 0.9, "via": "ocr"},
            {"name": query, "x": 140, "y": 320, "score": 0.9, "via": "ocr"},
        ]
        blocks = [
            {"text": "联系人", "bbox": (40, 120, 100, 140)},
            {"text": "搜索网络结果", "bbox": (40, 280, 160, 300)},
        ]
        picked = _pick_wechat_search_result_candidate(
            cands, query=query, all_blocks=blocks
        )
        self.assertIsNotNone(picked)
        self.assertEqual(int(picked["y"]), 160)
        self.assertIn("wechat_result_pick", str(picked.get("via") or ""))

    def test_falls_back_to_first_below_search_box(self):
        from windows_desktop_tools import _pick_wechat_search_result_candidate

        cands = [
            {"name": "张三", "x": 10, "y": 40, "score": 0.8, "via": "ocr"},
            {"name": "张三", "x": 10, "y": 200, "score": 0.8, "via": "ocr"},
        ]
        picked = _pick_wechat_search_result_candidate(cands, query="张三")
        self.assertEqual(int(picked["y"]), 200)


if __name__ == "__main__":
    unittest.main()
