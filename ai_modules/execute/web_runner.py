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

    默认失败：缺 URL/选择器不得软跳过当绿；显式 allow_skip 仅允许空导航，
    不得用于空 selector（Y3）。
    """
    from auth_batch_helpers import step_allows_skip

    action = (step.get("action") or "").strip().lower()
    sel = (step.get("selector") or step.get("selector_value", "") or "").strip()
    val = step.get("value") or step.get("input_value", "")
    t0 = time.perf_counter()
    ok = True
    err_msg: Optional[str] = None
    err_code: Optional[str] = None
    skipped = False

    def _fail(msg: str, code: str = "") -> Dict[str, Any]:
        return {
            "ok": False,
            "skipped": False,
            "error": msg,
            "error_code": code or None,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "action": action,
        }

    def _require_selector(action_label: str) -> Optional[Dict[str, Any]]:
        # 空选择器一律失败；allow_skip 也不能把缺 selector 当绿（Y3）
        if not sel:
            return _fail(
                f"{action_label} 步骤缺少 selector，不得软跳过",
                "EMPTY_SELECTOR",
            )
        return None

    try:
        if action in ("navigate", "goto"):
            url = (step.get("url") or val or "").strip()
            if not url or url == "__SKIP_URL__":
                if step_allows_skip(step):
                    skipped = True
                    ok = True
                    err_msg = None
                else:
                    return _fail(
                        "导航 URL 为空或为跳过占位符，未设置 allow_skip，不得假绿",
                        "EMPTY_URL",
                    )
            else:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        elif action in ("click", "tap", "dblclick", "double_click", "hover"):
            bad = _require_selector(action or "click")
            if bad:
                return bad
            if action in ("dblclick", "double_click"):
                page.dblclick(sel, timeout=timeout_ms)
            elif action == "hover":
                page.hover(sel, timeout=timeout_ms)
            else:
                page.click(sel, timeout=timeout_ms)
        elif action in ("fill", "input", "input_text", "type"):
            bad = _require_selector("input")
            if bad:
                return bad
            page.fill(sel, str(val), timeout=timeout_ms)
        elif action in ("select",):
            bad = _require_selector("select")
            if bad:
                return bad
            if val is None or str(val) == "":
                return _fail("select 步骤缺少选项值", "EMPTY_SELECT_VALUE")
            page.select_option(sel, str(val), timeout=timeout_ms)
        elif action == "wait":
            try:
                time.sleep(float(val or 1))
            except (ValueError, TypeError):
                time.sleep(1)
        elif action == "screenshot":
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
                "skipped": False,
                "error": None,
                "error_code": None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "action": action,
                "screenshot_path": ss_path,
            }
        elif action in ("extract_text", "extract", "get_text"):
            bad = _require_selector("extract_text")
            if bad:
                return bad
            loc = page.locator(sel)
            source = (step.get("source") or "text").strip().lower()
            if source in ("attribute", "attr"):
                attr = (step.get("attr") or step.get("attribute") or "value").strip()
                text = loc.get_attribute(attr, timeout=timeout_ms)
            elif source in ("input_value", "value"):
                text = loc.input_value(timeout=timeout_ms)
            else:
                text = loc.inner_text(timeout=timeout_ms)
            text = (text or "").strip()
            if not text and not step.get("allow_empty"):
                return _fail(f"extract_text 未取得非空文本: {sel}", "VAR_EXTRACT_MISSING")
            store_as = (
                step.get("store_as")
                or step.get("var_name")
                or step.get("extract_as")
                or ""
            )
            return {
                "ok": True,
                "skipped": False,
                "error": None,
                "error_code": None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "action": action,
                "extracted_text": text,
                "store_as": str(store_as or "").strip() or None,
            }
        elif action in ("verify", "assert"):
            compare_type = (step.get("compare_type") or "").strip().lower()
            expected = str(val).strip()
            if not compare_type:
                if sel:
                    compare_type = "visible"
                elif expected:
                    compare_type = "text_contains"
                else:
                    return _fail(
                        "assert 步骤缺少 selector/预期值，无法推断断言类型",
                        "EMPTY_ASSERT",
                    )

            if compare_type == "visible":
                if not sel:
                    return _fail("visible 断言缺少 selector", "EMPTY_SELECTOR")
                loc = page.locator(sel)
                if not loc.is_visible(timeout=timeout_ms):
                    ok = False
                    err_msg = f"元素不可见: {sel}"
                    err_code = "ASSERT_FAILED"
            elif compare_type == "text_contains":
                if not expected:
                    return _fail("text_contains 断言缺少预期值", "EMPTY_ASSERT")
                body_text = page.inner_text("body", timeout=timeout_ms) or ""
                if expected.lower() not in body_text.lower():
                    ok = False
                    err_msg = f"页面未包含预期文本: {expected[:80]}"
                    err_code = "ASSERT_FAILED"
            elif compare_type == "url_contains":
                if not expected:
                    return _fail("url_contains 断言缺少预期值", "EMPTY_ASSERT")
                current_url = page.url or ""
                if expected.lower() not in current_url.lower():
                    ok = False
                    err_msg = f"URL 不包含预期: {expected[:80]}（当前: {current_url[:80]}）"
                    err_code = "ASSERT_FAILED"
            elif compare_type == "title_contains":
                if not expected:
                    return _fail("title_contains 断言缺少预期值", "EMPTY_ASSERT")
                title = page.title() or ""
                if expected.lower() not in title.lower():
                    ok = False
                    err_msg = f"标题不包含预期: {expected[:80]}"
                    err_code = "ASSERT_FAILED"
            elif sel:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
                except Exception as e:
                    ok = False
                    err_msg = f"断言失败（元素不可见）: {sel[:80]} - {e}"
                    err_code = "ASSERT_FAILED"
            else:
                return _fail(
                    f"不支持的 assert compare_type 或缺少 selector: {compare_type}",
                    "EMPTY_ASSERT",
                )
        elif not action:
            return _fail("步骤缺少 action", "EMPTY_ACTION")
        else:
            err_msg = f"未识别的动作: {action}"
            err_code = "UNKNOWN_ACTION"
            ok = False
    except Exception as e:
        ok = False
        err_msg = str(e)[:200]
        err_code = err_code or "WEB_STEP_EXCEPTION"

    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "ok": ok,
        "skipped": skipped,
        "error": err_msg,
        "error_code": err_code,
        "elapsed_ms": elapsed,
        "action": action,
    }
