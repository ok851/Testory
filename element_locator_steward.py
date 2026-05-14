"""
元素定位管家：串联「页面快照 → 执行前解析（ai_locator_resolution）」与
「失败后兜底（ai_selector_recovery）」两条链路，便于离线验证与 CI。

用法：
  python -m element_locator_steward resolve --url https://example.com --plan steps.json
  python -m element_locator_steward recover --url https://example.com \\
      --describe \"点击登录\" --action click --failed \"button\"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

def _load_json_arg(path: Optional[str], stdin_fallback: bool) -> Any:
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if stdin_fallback:
        return json.load(sys.stdin)
    raise SystemExit("需要 --plan 文件或 stdin JSON")


def capture_snapshot_for_url(url: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """使用平台主 Playwright 线程抓取与 AI 规划一致的交互快照。"""
    from playwright_automation import (
        sync_automation_session_usable,
        sync_get_interactive_page_snapshot,
        sync_navigate_to,
        sync_start_browser,
    )

    u = (url or "").strip()
    if not u:
        return {}, "URL 为空"

    try:
        sync_start_browser(headless=True)
    except Exception:
        pass

    if not sync_automation_session_usable():
        return {}, "主浏览器未就绪：请先安装 Playwright 浏览器或在平台中启动会话"

    try:
        sync_navigate_to(u)
    except Exception as e:
        return {}, f"导航失败: {e}"

    try:
        snap = sync_get_interactive_page_snapshot(150)
    except Exception as e:
        return {}, f"抓取快照失败: {e}"

    if not isinstance(snap, dict):
        return {}, "快照格式异常"

    return snap, None


def run_resolve(
    url: str, steps: List[Any], snapshot_path: Optional[str]
) -> Tuple[List[Any], List[str], Dict[str, Any]]:
    from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot

    if snapshot_path:
        with open(snapshot_path, encoding="utf-8") as f:
            snap = json.load(f)
    else:
        snap, err = capture_snapshot_for_url(url)
        if err:
            raise RuntimeError(err)

    resolved, warns = resolve_plan_steps_locators_with_snapshot(steps, snap, force=True)
    return resolved, warns, snap


async def run_recover_async(
    url: str, description: str, action: str, failed_selector: str
) -> Tuple[Optional[Tuple[str, str]], Optional[str]]:
    from playwright.async_api import async_playwright

    from ai_selector_recovery import try_recover_selector_with_llm

    u = (url or "").strip()
    if not u:
        return None, "URL 为空"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1365, "height": 900})
            await page.goto(u, wait_until="load", timeout=45000)
            await page.wait_for_timeout(800)
            recovered = await try_recover_selector_with_llm(
                page, description, action, failed_selector
            )
            return recovered, None
        except Exception as e:
            return None, str(e)
        finally:
            await browser.close()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="元素定位管家 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("resolve", help="页面快照 + LLM 绑定步骤选择器（执行前解析）")
    pr.add_argument("--url", required=True, help="要打开的页面 URL")
    pr.add_argument("--plan", help="含 steps 数组的 JSON 文件；缺省从 stdin 读")
    pr.add_argument("--snapshot", help="已保存的快照 JSON（跳过打开浏览器）")
    pr.add_argument("--indent", type=int, default=2)

    rc = sub.add_parser("recover", help="失败后兜底：当前页控件表 + LLM 重新选定位")
    rc.add_argument("--url", required=True)
    rc.add_argument("--describe", required=True, help="步骤自然语言描述")
    rc.add_argument("--action", default="click")
    rc.add_argument("--failed", required=True, help="失败的主选择器")

    args = p.parse_args(argv)

    if args.cmd == "resolve":
        raw = _load_json_arg(args.plan, stdin_fallback=True)
        steps = raw.get("steps") if isinstance(raw, dict) else raw
        if not isinstance(steps, list):
            print("JSON 需包含 steps 数组或为步骤数组本身", file=sys.stderr)
            return 2
        try:
            resolved, warns, snap = run_resolve(args.url, steps, args.snapshot)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
        out = {"steps": resolved, "warnings": warns, "snapshot_meta": {
            "title": snap.get("title"),
            "url": snap.get("url"),
            "item_count": len(snap.get("items") or []),
        }}
        print(json.dumps(out, ensure_ascii=False, indent=args.indent))
        return 0

    if args.cmd == "recover":
        recovered, err = asyncio.run(
            run_recover_async(args.url, args.describe, args.action, args.failed)
        )
        if err:
            print(err, file=sys.stderr)
            return 1
        if not recovered:
            print(json.dumps({"recovered": None}, ensure_ascii=False))
            return 0
        print(
            json.dumps(
                {"recovered": {"selector_type": recovered[1], "selector_value": recovered[0]}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
