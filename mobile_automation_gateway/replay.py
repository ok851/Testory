# -*- coding: utf-8 -*-
"""
经插件 API 回放移动端步骤（v2 — 借鉴 SoloPi 的步骤结果增强与弹窗处理配置）。

Inspired by SoloPi:
  1. 步骤级计时与性能数据（Per-step timing & metadata）
  2. 弹窗自动处理可配置开关（Configurable dialog handling）
  3. 步骤结果附截图（Screenshot per step result）
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.mobile.mobile_automation import prepare_mobile_step
from mobile_automation_gateway import plugin_rpc
from modules.mobile.mobile_replay_context import (
    extract_context_package,
    infer_prepare_context,
    is_skippable_package,
    open_app_package,
    sanitize_replay_steps,
    should_skip_open_app_step,
)

_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "static" / "mobile_screenshots"

_VISION_ACTIONS = frozenset({
    "ai_tap",
    "ai_input",
    "assert_vision",
    "wait_vision",
    "extract_vision",
})


def _save_screenshot(data: bytes, udid: str, step_index: int) -> str:
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"replay_{udid.replace(':', '_')}_{int(time.time())}_{step_index}.jpg"
    path = _SCREENSHOT_DIR / name
    path.write_bytes(data)
    return f"/static/mobile_screenshots/{name}"


def _wait_after_action_ms() -> int:
    from modules.mobile.mobile_env_config import mobile_wait_after_action_ms

    return mobile_wait_after_action_ms()


def _capture_device_png(udid: str) -> Tuple[Optional[bytes], str, int, int]:
    try:
        img, meta = plugin_rpc.take_screenshot(udid)
        if isinstance(meta, dict):
            w = int(meta.get("width") or 1080)
            h = int(meta.get("height") or 1920)
        else:
            w, h = 1080, 1920
        return img, "", w, h
    except Exception as exc:
        return None, str(exc), 1080, 1920


def _sleep_after_action(action: str) -> None:
    if action in ("assert_vision", "wait_vision", "extract_vision"):
        return
    delay = _wait_after_action_ms()
    if delay > 0:
        time.sleep(delay / 1000.0)



def _execute_vision_step(udid: str, step: Dict[str, Any], *, step_index: int) -> Dict[str, Any]:
    action = (step.get("action") or "").strip().lower()
    desc = (step.get("description") or "").strip()
    locate = (step.get("locate_prompt") or desc).strip()

    try:
        if action == "ai_tap":
            if not locate:
                return {"status": "error", "error": "ai_tap 缺少元素描述", "action": action}
            from mobile_vision_tap import tap_mobile_by_description

            ok, msg = tap_mobile_by_description(udid, locate)
            if not ok:
                return {"status": "error", "error": msg, "action": action, "description": desc}
            _sleep_after_action(action)
            screenshot_url = ""
            try:
                img, _ = plugin_rpc.take_screenshot(udid)
                screenshot_url = _save_screenshot(img, udid, step_index)
            except Exception:
                pass
            return {
                "status": "success",
                "action": action,
                "message": msg,
                "screenshot": screenshot_url,
                "description": desc,
            }

        if action == "ai_input":
            text = (step.get("input_value") or "").strip()
            if not text:
                return {"status": "error", "error": "ai_input 缺少输入内容", "action": action}
            from mobile_vision_tap import tap_mobile_by_description, input_text_mobile

            if locate:
                ok, msg = tap_mobile_by_description(udid, locate)
                if not ok:
                    return {"status": "error", "error": msg, "action": action}
                _sleep_after_action("ai_tap")
            ok, msg = input_text_mobile(udid, text)
            if not ok:
                return {"status": "error", "error": msg, "action": action}
            screenshot_url = ""
            try:
                img, _ = plugin_rpc.take_screenshot(udid)
                screenshot_url = _save_screenshot(img, udid, step_index)
            except Exception:
                pass
            return {
                "status": "success",
                "action": action,
                "message": msg,
                "screenshot": screenshot_url,
            }

        if action in ("assert_vision", "wait_vision", "extract_vision"):
            png, err, w, h = _capture_device_png(udid)
            if not png:
                return {"status": "error", "error": f"截图失败: {err}", "action": action}
            from modules.ai.ai_vision_insight import vision_assert

            if action == "assert_vision":
                ok, detail = vision_assert(png, locate)
                return {
                    "status": "success" if ok else "error",
                    "action": action,
                    "message": detail,
                    "error": "" if ok else (detail or "视觉断言未匹配"),
                    "description": desc,
                }
            if action == "wait_vision":
                deadline = time.time() + 15.0
                while time.time() < deadline:
                    ok, detail = vision_assert(png, locate)
                    if ok:
                        return {
                            "status": "success",
                            "action": action,
                            "message": f"视觉等待完成: {detail}",
                        }
                    time.sleep(1.2)
                    png, err, w, h = _capture_device_png(udid)
                    if not png:
                        break
                return {
                    "status": "error",
                    "action": action,
                    "error": f"视觉等待超时: {locate[:60]}",
                }
            if action == "extract_vision":
                from modules.ai.ai_vision_insight import vision_extract

                text = vision_extract(png, locate)
                return {
                    "status": "success",
                    "action": action,
                    "extracted": text,
                    "description": desc,
                }

        return {"status": "error", "error": f"不支持的视觉操作: {action}", "action": action}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "action": action}


def replay_mobile_steps(
    udid: str,
    steps: List[Dict[str, Any]],
    *,
    handle_dialogs: bool = True,  # Inspired by SoloPi: configurable dialog handling
    step_timeout_ms: int = 30000,  # Inspired by SoloPi: per-step timeout
    max_retries: int = 3,  # Inspired by SoloPi: implicit wait retries
    continue_on_error: bool = False,
    screenshot_per_step: bool = False,
    on_step: Optional[callable] = None,
    on_dialog: Optional[callable] = None,
) -> Dict[str, Any]:
    """
    回放移动端步骤列表（v2 — 增强版）。

    Inspired by SoloPi:
      - handle_dialogs: 自动处理系统弹窗（默认开启）
      - step_timeout_ms: 单步超时
      - max_retries: 隐式等待重试次数
      - continue_on_error: 单步失败时是否继续执行后续步骤
      - screenshot_per_step: 是否每步截图（默认关闭以提升性能）
    """
    if not udid:
        return {"success": False, "error": "缺少 udid"}
    if not steps:
        return {"success": False, "error": "步骤列表为空"}

    steps = sanitize_replay_steps(steps)

    # 确保插件通道
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    if not ok:
        return {"success": False, "error": msg}

    try:
        from modules.mobile.mobile_adb_control import adb_press_home

        # 回放前退回桌面，避免助手 Activity 遮挡目标应用（设备端 RunSession 也会执行）。
        adb_press_home(udid)
        time.sleep(0.3)
    except Exception:
        pass

    ctx_pkg, ctx_required = infer_prepare_context(steps)
    if ctx_pkg and not is_skippable_package(ctx_pkg):
        try:
            from modules.mobile.mobile_adb_control import adb_get_foreground_package, adb_launch_app

            if adb_get_foreground_package(udid) != ctx_pkg:
                adb_launch_app(udid, ctx_pkg, wait_foreground=True, timeout_sec=10.0)
        except Exception as exc:
            if ctx_required:
                return {
                    "success": False,
                    "error": str(exc),
                    "error_code": "LAUNCH_TIMEOUT",
                }

    step_results: List[Dict[str, Any]] = []
    total_start = time.time()

    for i, raw_step in enumerate(steps):
        step_index = i + 1
        step_start = time.time()

        # 准备步骤
        step = prepare_mobile_step(dict(raw_step))
        action = (step.get("action") or "").strip().lower()

        result: Dict[str, Any] = {
            "step_order": step_index,
            "action": action,
            "description": step.get("description", ""),
            "timestamp": int(time.time()),
        }

        try:
            if action == "open_app":
                if should_skip_open_app_step(step):
                    result["status"] = "success"
                    result["message"] = "已跳过启动器/系统自动切换步骤"
                    step_results.append(result)
                    continue
            # Inspired by SoloPi: 每步前自动尝试处理系统弹窗
            if handle_dialogs and action not in ("dialog", "open_app", "press_home", "press_back"):
                try:
                    plugin_rpc.dismiss_dialogs(udid)
                except Exception:
                    pass

            # 视觉步骤走专用通道
            if action in _VISION_ACTIONS:
                vis_result = _execute_vision_step(udid, step, step_index=step_index)
                result.update(vis_result)
            else:
                # 回放完全交由设备端执行，PC 端只收集结果
                replay_result = None
                for attempt in range(max(1, max_retries)):
                    replay_result = plugin_rpc.replay_step(udid, step, step_index=step_index)
                    if replay_result.get("status") != "error":
                        break
                    time.sleep(0.35)
                result.update(replay_result or {})

            # Inspired by SoloPi: 截图已由设备端步骤回调提供（如有）
            if screenshot_per_step:
                try:
                    img, _ = plugin_rpc.take_screenshot(udid)
                    result["screenshot"] = _save_screenshot(img, udid, step_index)
                except Exception:
                    pass

        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)

        # Inspired by SoloPi: 记录步骤耗时
        result["duration_ms"] = int((time.time() - step_start) * 1000)

        step_results.append(result)

        if on_step:
            try:
                on_step(step_index, result)
            except Exception:
                pass

        # 失败时决定是否继续
        if result.get("status") == "error":
            if not continue_on_error:
                return {
                    "success": False,
                    "total": len(steps),
                    "failed": step_index,
                    "duration_ms": int((time.time() - total_start) * 1000),
                    "error": result.get("error", f"第 {step_index} 步执行失败"),
                    "step_results": step_results,
                }

    return {
        "success": True,
        "total": len(steps),
        "failed": 0,
        "duration_ms": int((time.time() - total_start) * 1000),
        "step_results": step_results,
    }


def run_steps(
    udid: str,
    steps: List[Dict[str, Any]],
    *,
    from_index: int = 0,
    handle_dialogs: bool = True,
    step_timeout_ms: int = 30000,
    max_retries: int = 3,
    continue_on_error: bool = False,
    screenshot_per_step: bool = False,
) -> Dict[str, Any]:
    """Gateway /internal/replay/run 入口。"""
    subset = steps[from_index:] if from_index > 0 else steps
    return replay_mobile_steps(
        udid,
        subset,
        handle_dialogs=handle_dialogs,
        step_timeout_ms=step_timeout_ms,
        max_retries=max_retries,
        continue_on_error=continue_on_error,
        screenshot_per_step=screenshot_per_step,
    )


def execute_step(udid: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    """Gateway /internal/replay/step 入口。"""
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    if not ok:
        return {"status": "error", "error": msg}
    prepared = prepare_mobile_step(dict(step))
    for attempt in range(3):
        result = plugin_rpc.replay_step(udid, prepared, step_index=step_index)
        if result.get("status") != "error":
            return result
        time.sleep(0.4)
    return result
