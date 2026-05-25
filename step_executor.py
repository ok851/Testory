# -*- coding: utf-8 -*-
"""
统一步骤路由器：按 automation_layer 分发 Web（Playwright）与桌面（pywinauto）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from desktop_automation import (
    normalize_automation_layer,
    sync_desktop_execute_step,
    validate_step_for_layer,
)
from desktop_runtime import desktop_runtime_available, parse_desktop_spec

try:
    from playwright_automation import resolve_playwright_headless
except ImportError:

    def resolve_playwright_headless(requested: bool = True) -> bool:
        return requested


def case_steps_include_desktop(steps: List[Dict[str, Any]]) -> bool:
    return any(normalize_automation_layer(s) == "desktop" for s in (steps or []))


def case_steps_include_web(steps: List[Dict[str, Any]]) -> bool:
    """用例是否包含 Web 步骤（纯桌面用例为 False，不应启动 Playwright）。"""
    return any(normalize_automation_layer(s) != "desktop" for s in (steps or []))


def ensure_mixed_run_environment(steps: List[Dict[str, Any]]) -> Optional[str]:
    """
    混排用例运行前检查。返回 None 表示通过；否则为错误/警告文案。
 若含桌面步骤且 Playwright 为无头，返回警告（调用方可选择强制有界面）。
    """
    if not case_steps_include_desktop(steps):
        return None
    if not desktop_runtime_available():
        return (
            "用例包含桌面自动化步骤，但当前环境不支持（需 Windows 且已安装 opencv-python、mss）。"
        )
    if resolve_playwright_headless(True):
        return (
            "用例包含桌面与 Web 混排步骤：请将 PLAYWRIGHT_HEADLESS 设为 0，"
            "并在有交互桌面的用户会话中运行平台。"
        )
    return None


def enrich_execution_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """将数据库步骤格式补充 automation_layer / desktop_spec 到执行脚本 dict。"""
    out = dict(step)
    out["automation_layer"] = normalize_automation_layer(step)
    ds = step.get("desktop_spec")
    if ds and not isinstance(ds, dict):
        out["desktop_spec"] = parse_desktop_spec(ds)
    elif isinstance(ds, dict):
        out["desktop_spec"] = ds
    else:
        out["desktop_spec"] = {}
    return out


def is_desktop_step(step: Dict[str, Any]) -> bool:
    return normalize_automation_layer(step) == "desktop"


DESKTOP_POINTER_ACTIONS = frozenset({"click", "double_click", "right_click"})


def validate_desktop_step_result(result: Any, action: str) -> Dict[str, Any]:
    """
    统一桌面步骤成功闸门：status 成功；指针步骤另要求 verified 与 pointer_executed。
    所有执行入口（单用例、批量、脚本）应调用此函数。
    """
    act = (action or "").strip().lower()
    if not isinstance(result, dict):
        raise RuntimeError(
            f"桌面步骤返回无效结果（期望 dict，得到 {type(result).__name__}）"
        )
    status = str(result.get("status") or "success").strip().lower()
    if status not in ("success", "ok", "passed"):
        raise RuntimeError(result.get("error") or "桌面步骤执行失败")
    if act in DESKTOP_POINTER_ACTIONS:
        if not result.get("verified"):
            raise RuntimeError(
                result.get("error")
                or "桌面指针步骤未通过执行校验（可能未命中目标控件）"
            )
        if not result.get("pointer_executed"):
            raise RuntimeError(
                result.get("error")
                or "桌面指针步骤未真正执行（pointer_executed=false）"
            )
    return result


def sync_execute_step_by_layer(
    step: Dict[str, Any],
    *,
    web_executor: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    同步执行单步（桌面直接执行；Web 由调用方传入 web_executor，因 app.py 逻辑仍在原位）。
    返回桌面执行结果 dict；Web 路径由 web_executor 负责，返回 {"status": "delegated_web"}。
    """
    layer = normalize_automation_layer(step)
    action = (step.get("action") or "").strip()
    err = validate_step_for_layer(action, layer)
    if err:
        raise ValueError(err)

    if layer == "desktop":
        result = sync_desktop_execute_step(step)
        return validate_desktop_step_result(result, action)

    if web_executor:
        web_executor(step)
        return {"status": "delegated_web"}
    raise ValueError("Web 步骤需要提供 web_executor 回调")


async def async_execute_step_by_layer(
    step: Dict[str, Any],
    automation: Any,
) -> List[Dict[str, Any]]:
    """
    批量/Playwright 路径：桌面步骤走 sync；Web 步骤走 automation.execute_single_step。
    """
    import asyncio

    exec_step = enrich_execution_step(step)
    action = (exec_step.get("action") or "").strip()
    err = validate_step_for_layer(action, normalize_automation_layer(exec_step))
    if err:
        raise ValueError(err)

    if is_desktop_step(exec_step):
        result = await asyncio.to_thread(sync_desktop_execute_step, exec_step)
        return [validate_desktop_step_result(result, action)]

    results = await automation.execute_single_step(exec_step)
    return results if isinstance(results, list) else [results]
