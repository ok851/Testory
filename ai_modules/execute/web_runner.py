# -*- coding: utf-8 -*-
"""Web Playwright 执行封装：复用平台 PlaywrightAutomation 执行管道。

优化点：
1. 完整支持 9 种断言类型（text_equals / text_contains / text_regex /
   element_exists / element_visible / element_attr / element_count /
   url_equals / url_contains），替代之前的 4 种残缺实现。
2. 文本断言支持 selector 精准定位 + 等待机制，不再只搜 body 全文。
3. extract_text 添加文本轮询等待（非空重试），适应异步渲染页面。
4. 断言失败输出 实际值 vs 期望值 对比，便于调试。
5. 断言失败自动截图（可通过 step.no_screenshot 关闭）。
6. 区分"元素不存在"与"文本为空"的错误码。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from logger import uat_logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    action: str,
    ok: bool,
    elapsed_ms: float,
    *,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    skipped: bool = False,
    **extra: Any,
) -> Dict[str, Any]:
    """统一构造步骤结果字典。"""
    r: Dict[str, Any] = {
        "ok": ok,
        "skipped": skipped,
        "error": error,
        "error_code": error_code,
        "elapsed_ms": elapsed_ms,
        "action": action,
    }
    r.update(extra)
    return r


def _capture_failure_screenshot(page: Any, action: str, step: Dict[str, Any]) -> Optional[str]:
    """断言/步骤失败时自动截图，返回截图路径。关闭时返回 None。"""
    if step.get("no_screenshot"):
        return None
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        ss_dir = os.path.join(
            os.environ.get("UAT_DATA_DIR", "."), "stage_screenshots"
        )
        os.makedirs(ss_dir, exist_ok=True)
        ss_path = os.path.join(ss_dir, f"web_fail_{action}_{ts}.png")
        page.screenshot(path=ss_path, type="png")
        return ss_path
    except Exception:
        return None


def _wait_for_text_nonempty(
    locator: Any,
    timeout_ms: int,
    source: str = "text",
    attr: str = "",
    poll_interval_ms: int = 200,
) -> Tuple[Optional[str], bool]:
    """轮询等待 locator 的文本变为非空。

    返回 (text, element_found)：
    - element_found=False  → 元素根本不存在/不可见
    - element_found=True 且 text="" → 元素存在但文本始终为空
    - element_found=True 且 text!= "" → 成功
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    last_text = ""
    element_seen = False

    while time.monotonic() < deadline:
        try:
            if source in ("attribute", "attr"):
                last_text = locator.get_attribute(attr) or ""
            elif source in ("input_value", "value"):
                last_text = locator.input_value() or ""
            else:
                last_text = locator.inner_text() or ""
            last_text = last_text.strip()
            element_seen = True
            if last_text:
                return last_text, True
        except Exception:
            pass
        time.sleep(poll_interval_ms / 1000.0)

    # 超时：如果从未成功读取过，说明元素可能不存在
    return last_text, element_seen


# ---------------------------------------------------------------------------
# Assertion handlers (each returns (ok: bool, err_msg: str, actual: Any))
# ---------------------------------------------------------------------------

def _assert_visible(
    page: Any, sel: str, timeout_ms: int
) -> Tuple[bool, str, Any]:
    """断言元素可见。"""
    loc = page.locator(sel)
    try:
        visible = loc.is_visible(timeout=timeout_ms)
        if visible:
            return True, "", True
        return False, f"元素不可见: {sel[:120]}", False
    except Exception as e:
        return False, f"元素不存在或不可见: {sel[:120]} - {e}", None


def _assert_element_exists(
    page: Any, sel: str, timeout_ms: int
) -> Tuple[bool, str, Any]:
    """断言元素存在（可见或隐藏均可）。"""
    try:
        page.wait_for_selector(sel, state="attached", timeout=timeout_ms)
        return True, "", True
    except Exception:
        return False, f"元素不存在: {sel[:120]}", False


def _assert_text_equals(
    page: Any,
    sel: str,
    expected: str,
    timeout_ms: int,
    ignore_case: bool = True,
) -> Tuple[bool, str, Any]:
    """断言文本精确相等。有 selector 则精准匹配，否则搜 body 全文。"""
    if sel:
        loc = page.locator(sel)
        text, seen = _wait_for_text_nonempty(loc, timeout_ms)
        if not seen:
            return False, f"元素不存在或无文本: {sel[:120]}", None
    else:
        try:
            text = page.inner_text("body", timeout=timeout_ms) or ""
        except Exception:
            text = ""

    actual = text.strip()
    if ignore_case:
        ok = actual.lower() == expected.lower()
    else:
        ok = actual == expected
    if ok:
        return True, "", actual
    return False, f"文本不相等: 期望 {expected!r}, 实际 {actual!r}", actual


def _assert_text_contains(
    page: Any,
    sel: str,
    expected: str,
    timeout_ms: int,
    ignore_case: bool = True,
) -> Tuple[bool, str, Any]:
    """断言文本包含。有 selector 则在指定元素内查找，否则搜 body 全文。"""
    if sel:
        loc = page.locator(sel)
        text, seen = _wait_for_text_nonempty(loc, timeout_ms)
        if not seen:
            return False, f"元素不存在或无文本: {sel[:120]}", None
    else:
        try:
            text = page.inner_text("body", timeout=timeout_ms) or ""
        except Exception:
            text = ""

    actual = text.strip()
    if ignore_case:
        ok = expected.lower() in actual.lower()
    else:
        ok = expected in actual
    if ok:
        return True, "", actual
    snippet = actual[:100] + ("..." if len(actual) > 100 else "")
    return (
        False,
        f"文本未包含期望: 期望 {expected!r}, 实际 {snippet!r}",
        actual,
    )


def _assert_text_regex(
    page: Any,
    sel: str,
    pattern: str,
    timeout_ms: int,
) -> Tuple[bool, str, Any]:
    """断言文本正则匹配。"""
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return False, f"正则表达式错误: {e}", None

    if sel:
        loc = page.locator(sel)
        text, seen = _wait_for_text_nonempty(loc, timeout_ms)
        if not seen:
            return False, f"元素不存在或无文本: {sel[:120]}", None
    else:
        try:
            text = page.inner_text("body", timeout=timeout_ms) or ""
        except Exception:
            text = ""

    actual = text.strip()
    m = compiled.search(actual)
    if m:
        return True, f"正则匹配成功: {m.group(0)!r}", actual
    return False, f"正则未匹配: 模式 {pattern!r}, 实际 {actual[:100]!r}", actual


def _assert_element_attr(
    page: Any,
    sel: str,
    attr_name: str,
    expected_value: str,
    timeout_ms: int,
    operator: str = "equals",
) -> Tuple[bool, str, Any]:
    """断言元素属性值。"""
    loc = page.locator(sel)
    try:
        page.wait_for_selector(sel, state="attached", timeout=timeout_ms)
    except Exception:
        return False, f"元素不存在: {sel[:120]}", None

    actual = loc.get_attribute(attr_name) or ""
    actual = actual.strip()

    if operator == "equals":
        ok = actual == expected_value
    elif operator == "contains":
        ok = expected_value in actual
    elif operator == "regex":
        try:
            ok = bool(re.search(expected_value, actual))
        except re.error:
            ok = False
    else:
        ok = False

    if ok:
        return True, "", actual
    return (
        False,
        f"属性 {attr_name} 不符合: 期望 {expected_value!r}, 实际 {actual!r}",
        actual,
    )


def _assert_element_count(
    page: Any,
    sel: str,
    expected_count: int,
    timeout_ms: int,
    operator: str = "gte",
) -> Tuple[bool, str, Any]:
    """断言元素数量。默认 gte（>=），适配列表/表格至少 N 条的常见场景。"""
    try:
        page.wait_for_selector(sel, state="attached", timeout=timeout_ms)
    except Exception:
        # 等待超时，元素可能不存在
        count = 0
    else:
        count = page.locator(sel).count()

    op = operator or "gte"
    if op == "equals":
        ok = count == expected_count
    elif op == "gt":
        ok = count > expected_count
    elif op == "lt":
        ok = count < expected_count
    elif op == "gte":
        ok = count >= expected_count
    elif op == "lte":
        ok = count <= expected_count
    else:
        ok = count == expected_count

    if ok:
        return True, "", count
    return (
        False,
        f"元素数量不符合: 实际 {count}, 期望 {op} {expected_count}",
        count,
    )


def _assert_url_contains(
    page: Any, expected: str
) -> Tuple[bool, str, Any]:
    """断言 URL 包含子串。"""
    actual = page.url or ""
    if expected.lower() in actual.lower():
        return True, "", actual
    return False, f"URL 未包含: 期望 {expected!r}, 实际 {actual!r}", actual


def _assert_url_equals(
    page: Any, expected: str
) -> Tuple[bool, str, Any]:
    """断言 URL 精确相等。"""
    actual = page.url or ""
    if actual.rstrip("/") == expected.rstrip("/"):
        return True, "", actual
    return False, f"URL 不相等: 期望 {expected!r}, 实际 {actual!r}", actual


def _assert_title_contains(
    page: Any, expected: str
) -> Tuple[bool, str, Any]:
    """断言页面标题包含子串。"""
    actual = page.title() or ""
    if expected.lower() in actual.lower():
        return True, "", actual
    return False, f"标题未包含: 期望 {expected!r}, 实际 {actual!r}", actual


# ---------------------------------------------------------------------------
# Assertion dispatch
# ---------------------------------------------------------------------------

def _run_assertion(
    page: Any,
    step: Dict[str, Any],
    *,
    timeout_ms: int,
) -> Tuple[bool, str, Any, Optional[str]]:
    """Dispatch assertion based on compare_type. Returns (ok, err_msg, actual, err_code)."""
    compare_type = (step.get("compare_type") or "").strip().lower()
    sel = (step.get("selector") or step.get("selector_value", "") or "").strip()
    val = step.get("value") or step.get("input_value", "")
    expected = str(val).strip()

    # Auto-detect
    if not compare_type:
        if sel:
            compare_type = "visible"
        elif expected:
            compare_type = "text_contains"
        else:
            return False, "assert 步骤缺少 selector/预期值，无法推断断言类型", None, "EMPTY_ASSERT"

    try:
        if compare_type == "visible":
            if not sel:
                return False, "visible 断言缺少 selector", None, "EMPTY_SELECTOR"
            ok, msg, actual = _assert_visible(page, sel, timeout_ms)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "element_exists":
            if not sel:
                return False, "element_exists 断言缺少 selector", None, "EMPTY_SELECTOR"
            ok, msg, actual = _assert_element_exists(page, sel, timeout_ms)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "text_equals":
            if not expected:
                return False, "text_equals 断言缺少预期值", None, "EMPTY_ASSERT"
            ignore_case = not step.get("case_sensitive", False)
            ok, msg, actual = _assert_text_equals(
                page, sel, expected, timeout_ms, ignore_case=ignore_case
            )
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "text_contains":
            if not expected:
                return False, "text_contains 断言缺少预期值", None, "EMPTY_ASSERT"
            ignore_case = not step.get("case_sensitive", False)
            ok, msg, actual = _assert_text_contains(
                page, sel, expected, timeout_ms, ignore_case=ignore_case
            )
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "text_regex":
            if not expected:
                return False, "text_regex 断言缺少正则模式", None, "EMPTY_ASSERT"
            ok, msg, actual = _assert_text_regex(page, sel, expected, timeout_ms)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "element_attr":
            if not sel:
                return False, "element_attr 断言缺少 selector", None, "EMPTY_SELECTOR"
            attr_name = str(
                step.get("attr_name") or step.get("attr") or step.get("attribute") or ""
            ).strip()
            if not attr_name:
                return False, "element_attr 断言缺少属性名", None, "EMPTY_ASSERT"
            attr_expected = str(
                step.get("expected_value") if "expected_value" in step else (val or "")
            ).strip()
            operator = str(step.get("operator") or "equals").strip().lower()
            ok, msg, actual = _assert_element_attr(
                page, sel, attr_name, attr_expected, timeout_ms, operator=operator
            )
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "element_count":
            if not sel:
                return False, "element_count 断言缺少 selector", None, "EMPTY_SELECTOR"
            try:
                raw_count = step.get("expected_count") if "expected_count" in step else val
                exp_count = int(raw_count or 0)
            except (ValueError, TypeError):
                exp_count = 0
            operator = str(step.get("operator") or "gte").strip().lower()
            ok, msg, actual = _assert_element_count(
                page, sel, exp_count, timeout_ms, operator=operator
            )
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "url_contains":
            if not expected:
                return False, "url_contains 断言缺少预期值", None, "EMPTY_ASSERT"
            ok, msg, actual = _assert_url_contains(page, expected)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "url_equals":
            if not expected:
                return False, "url_equals 断言缺少预期值", None, "EMPTY_ASSERT"
            ok, msg, actual = _assert_url_equals(page, expected)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        elif compare_type == "title_contains":
            if not expected:
                return False, "title_contains 断言缺少预期值", None, "EMPTY_ASSERT"
            ok, msg, actual = _assert_title_contains(page, expected)
            return ok, msg, actual, None if ok else "ASSERT_FAILED"

        else:
            # Fallback: treat unknown compare_type as visible if selector present
            if sel:
                ok, msg, actual = _assert_visible(page, sel, timeout_ms)
                if ok:
                    return True, "", actual, None
                return (
                    False,
                    f"不支持的断言类型 {compare_type!r}，已 fallback 为可见性检查: {msg}",
                    actual,
                    "ASSERT_FAILED",
                )
            return (
                False,
                f"不支持的 assert compare_type: {compare_type}",
                None,
                "EMPTY_ASSERT",
            )

    except Exception as e:
        return False, f"断言执行异常: {e}", None, "WEB_STEP_EXCEPTION"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
        out = {
            "action": "assert",
            "selector": sel,
            "selector_type": step.get("selector_type", "css"),
            "input_value": str(val),
            "compare_type": step.get("compare_type", ""),
            "description": desc,
        }
        # Preserve additional assertion params
        for key in (
            "expected_value",
            "expected_count",
            "attr_name",
            "operator",
            "case_sensitive",
        ):
            if key in step:
                out[key] = step[key]
        return out
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

    def _fail(msg: str, code: str = "") -> Dict[str, Any]:
        ss = _capture_failure_screenshot(page, action, step)
        return _make_result(
            action,
            False,
            round((time.perf_counter() - t0) * 1000, 1),
            error=msg,
            error_code=code or None,
            screenshot_path=ss,
        )

    def _require_selector(action_label: str) -> Optional[Dict[str, Any]]:
        if not sel:
            return _fail(
                f"{action_label} 步骤缺少 selector",
                "EMPTY_SELECTOR",
            )
        return None

    try:
        if action in ("navigate", "goto"):
            url = (step.get("url") or val or "").strip()
            if not url or url == "__SKIP_URL__":
                if step_allows_skip(step):
                    return _make_result(
                        action, True, round((time.perf_counter() - t0) * 1000, 1), skipped=True
                    )
                return _fail(
                    "导航 URL 为空或为跳过占位符，未设置 allow_skip",
                    "EMPTY_URL",
                )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return _make_result(
                action, True, round((time.perf_counter() - t0) * 1000, 1)
            )

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
            return _make_result(
                action, True, round((time.perf_counter() - t0) * 1000, 1)
            )

        elif action in ("fill", "input", "input_text", "type"):
            bad = _require_selector("input")
            if bad:
                return bad
            page.fill(sel, str(val), timeout=timeout_ms)
            return _make_result(
                action, True, round((time.perf_counter() - t0) * 1000, 1)
            )

        elif action in ("select",):
            bad = _require_selector("select")
            if bad:
                return bad
            if val is None or str(val) == "":
                return _fail("select 步骤缺少选项值", "EMPTY_SELECT_VALUE")
            page.select_option(sel, str(val), timeout=timeout_ms)
            return _make_result(
                action, True, round((time.perf_counter() - t0) * 1000, 1)
            )

        elif action == "wait":
            try:
                time.sleep(float(val or 1))
            except (ValueError, TypeError):
                time.sleep(1)
            return _make_result(
                action, True, round((time.perf_counter() - t0) * 1000, 1)
            )

        elif action == "screenshot":
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            ss_dir = os.path.join(
                os.environ.get("UAT_DATA_DIR", "."), "stage_screenshots"
            )
            os.makedirs(ss_dir, exist_ok=True)
            ss_path = os.path.join(ss_dir, f"web_stage_{ts}.png")
            try:
                page.screenshot(path=ss_path, type="png")
            except Exception:
                ss_path = None
            return _make_result(
                action,
                True,
                round((time.perf_counter() - t0) * 1000, 1),
                screenshot_path=ss_path,
            )

        elif action in ("extract_text", "extract", "get_text"):
            bad = _require_selector("extract_text")
            if bad:
                return bad
            loc = page.locator(sel)
            source = (step.get("source") or "text").strip().lower()
            attr = (step.get("attr") or step.get("attribute") or "value").strip()

            text, element_seen = _wait_for_text_nonempty(
                loc, timeout_ms, source=source, attr=attr
            )

            if not element_seen:
                return _fail(
                    f"extract_text 元素不存在或不可见: {sel[:120]}",
                    "ELEMENT_NOT_FOUND",
                )
            if not text and not step.get("allow_empty"):
                return _fail(
                    f"extract_text 文本为空: {sel[:120]}",
                    "TEXT_EMPTY",
                )

            store_as = (
                step.get("store_as")
                or step.get("var_name")
                or step.get("extract_as")
                or ""
            )
            return _make_result(
                action,
                True,
                round((time.perf_counter() - t0) * 1000, 1),
                extracted_text=text,
                store_as=str(store_as or "").strip() or None,
            )

        elif action in ("verify", "assert"):
            ok, err_msg, actual, err_code = _run_assertion(
                page, step, timeout_ms=timeout_ms
            )
            if ok:
                return _make_result(
                    action,
                    True,
                    round((time.perf_counter() - t0) * 1000, 1),
                    actual_value=actual,
                )
            ss = _capture_failure_screenshot(page, action, step)
            return _make_result(
                action,
                False,
                round((time.perf_counter() - t0) * 1000, 1),
                error=err_msg,
                error_code=err_code,
                actual_value=actual,
                screenshot_path=ss,
            )

        elif not action:
            return _fail("步骤缺少 action", "EMPTY_ACTION")

        else:
            return _fail(f"未识别的动作: {action}", "UNKNOWN_ACTION")

    except Exception as e:
        return _fail(f"执行异常: {e}", "WEB_STEP_EXCEPTION")
