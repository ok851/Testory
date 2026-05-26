#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演示：CDP 连接 → 打开百度 → 构造元素定义 → （可选）执行输入。

用法:
  python examples/web_capture_baidu_demo.py --dry-run
  WEB_CAPTURE_EXEC_MODE=cdp python examples/web_capture_baidu_demo.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅打印元素 JSON，不启动浏览器")
    parser.add_argument("--browser", default="edge", choices=["edge", "chrome"])
    args = parser.parse_args()

    from web_capture.locator_generator import format_dom_pick_payload

    sample_pick = {
        "selector": "#kw",
        "elementInfo": {
            "tagName": "INPUT",
            "id": "kw",
            "className": "",
            "textContent": "",
            "attributes": {"name": "wd"},
        },
        "source_url": "https://www.baidu.com",
    }
    defn = format_dom_pick_payload(sample_pick, capture_mode="cdp")
    print(json.dumps(defn, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    from web_capture import cdp_browser
    from web_capture.cdp_executor import fill_async, click_async
    import asyncio

    launch = cdp_browser.launch_debug_browser(browser=args.browser, url="https://www.baidu.com")
    if not launch.get("success"):
        print("launch failed:", launch.get("error"), file=sys.stderr)
        return 1
    conn = cdp_browser.connect_playwright_over_cdp(launch["debug_port"])
    if not conn.get("success"):
        print("connect failed:", conn.get("error"), file=sys.stderr)
        return 1

    os.environ["WEB_CAPTURE_EXEC_MODE"] = "cdp"
    asyncio.run(fill_async("id", "kw", "Playwright CDP demo"))
    asyncio.run(click_async("css", "#su"))
    print("Demo completed: filled #kw and clicked search button")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
