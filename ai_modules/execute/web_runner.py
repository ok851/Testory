# -*- coding: utf-8 -*-
"""Web Playwright 执行封装：复用平台 PlaywrightAutomation 执行管道。"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from logger import uat_logger


def _convert_orchestrator_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """将编排器 stage step 格式转为 execute_script_steps 可消费的格式。"""
    action = (step.get("action") or "").strip().lower()
    sel = step.get("selector") or step.get("selector_value", "")
    val = step.get("value") or step.get("input_value", "")
    url = step.get("url", "")
    desc = step.get("description", "")

    if action in ("navigate", "goto"):
        return {"action": "navigate", "url": url or val, "description": desc}
    if action in ("click", "tap"):
        return {
            "action": "click",
            "selector": sel,
            "selector_type": step.get("selector_type", "css"),
            "description": desc,
        }
    if action in ("fill", "input", "input_text"):
        return {
            "action": "input",
            "selector": sel,
            "selector_type": step.get("selector_type", "css"),
            "input_value": str(val),
            "description": desc,
        }
    if action in ("select",):
        return {
            "action": "select",
            "selector": sel,
            "selector_type": step.get("selector_type", "css"),
            "input_value": str(val),
            "description": desc,
        }
    if action == "wait":
        try:
            ms = int(float(val) * 1000)
        except (ValueError, TypeError):
            ms = 1000
        return {"action": "wait", "time": ms, "description": desc}
    if action == "screenshot":
        return {"action": "screenshot", "description": desc}
    if action in ("verify", "assert"):
        return {
            "action": "assert",
            "selector": sel,
            "selector_type": step.get("selector_type", "css"),
            "input_value": str(val),
            "description": desc,
        }
    # 原样透传未知动作
    return dict(step)


def run_web_case_steps(
    steps: List[Dict[str, Any]],
    automation: Any,
) -> List[Dict[str, Any]]:
    """
    批量 Web 步骤执行入口。
    调用方传入 PlaywrightAutomation 实例，由本函数完成格式转换后
    委托 automation.execute_script_steps 串行执行。

    返回每步执行结果列表。
    """
    if not steps:
        return []
    if automation is None:
        raise ValueError("run_web_case_steps 需要有效的 PlaywrightAutomation 实例")

    converted = [_convert_orchestrator_step(s) for s in steps if isinstance(s, dict)]
    if not converted:
        return []

    t0 = time.perf_counter()
    try:
        results = automation.execute_script_steps(converted)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        uat_logger.info(
            "web_runner: %d 步执行完成，耗时 %sms", len(converted), elapsed
        )
        return results if isinstance(results, list) else []
    except Exception as e:
        uat_logger.warning("web_runner: 执行异常 %s", e)
        raise


def execute_single_web_step(
    step: Dict[str, Any],
    page: Any,
    *,
    timeout_ms: int = 5000,
) -> Dict[str, Any]:
    """
    直接用 Playwright Page 对象执行单个 Web 步骤。
    用于编排器中不需要完整 automation 实例的轻量场景。
    """
    action = (step.get("action") or "").strip().lower()
    sel = step.get("selector") or step.get("selector_value", "")
    val = step.get("value") or step.get("input_value", "")
    t0 = time.perf_counter()
    ok = True
    err_msg: Optional[str] = None

    try:
        if action in ("navigate", "goto"):
            url = step.get("url") or val
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        elif action in ("click", "tap"):
            if sel:
                page.click(sel, timeout=timeout_ms)
        elif action in ("fill", "input", "input_text"):
            if sel:
                page.fill(sel, str(val), timeout=timeout_ms)
        elif action in ("select",):
            if sel and val:
                page.select_option(sel, str(val), timeout=timeout_ms)
        elif action == "wait":
            try:
                time.sleep(float(val or 1))
            except (ValueError, TypeError):
                time.sleep(1)
        elif action == "screenshot":
            # 截图并保存到磁盘，返回路径
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            ss_dir = os.path.join(os.environ.get("UAT_DATA_DIR", "."), "stage_screenshots")
            os.makedirs(ss_dir, exist_ok=True)
            ss_path = os.path.join(ss_dir, f"web_stage_{ts}.png")
            try:
                page.screenshot(path=ss_path, type="png")
            except Exception:
                ss_path = None
            return {
                "ok": True,
                "error": None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "action": action,
                "screenshot_path": ss_path,
            }
        elif action in ("verify", "assert"):
            # 断言：根据 compare_type / input_value 检查页面状态
            compare_type = (step.get("compare_type") or "").strip().lower()
            expected = str(val).strip()
            if not compare_type:
                # 自动推断：有 selector 则检查可见性，有 input_value 则检查文本
                if sel:
                    compare_type = "visible"
                elif expected:
                    compare_type = "text_contains"
                else:
                    compare_type = "url_contains"

            if compare_type == "visible" and sel:
                loc = page.locator(sel)
                if not loc.is_visible(timeout=timeout_ms):
                    ok = False
                    err_msg = f"元素不可见: {sel}"
            elif compare_type == "text_contains" and expected:
                body_text = page.inner_text("body", timeout=timeout_ms) or ""
                if expected.lower() not in body_text.lower():
                    ok = False
                    err_msg = f"页面未包含预期文本: {expected[:80]}"
            elif compare_type == "url_contains" and expected:
                current_url = page.url or ""
                if expected.lower() not in current_url.lower():
                    ok = False
                    err_msg = f"URL 不包含预期: {expected[:80]}（当前: {current_url[:80]}）"
            elif compare_type == "title_contains" and expected:
                title = page.title() or ""
                if expected.lower() not in title.lower():
                    ok = False
                    err_msg = f"标题不包含预期: {expected[:80]}"
            elif sel:
                # 默认：检查元素是否存在且可见
                try:
                    page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
                except Exception as e:
                    ok = False
                    err_msg = f"断言失败（元素不可见）: {sel[:80]} - {e}"
        else:
            err_msg = f"未识别的动作: {action}"
            ok = False
    except Exception as e:
        ok = False
        err_msg = str(e)[:200]

    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "ok": ok,
        "error": err_msg,
        "elapsed_ms": elapsed,
        "action": action,
    }
