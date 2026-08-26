# -*- coding: utf-8 -*-
"""移动端步骤执行封装：支持 Android (Appium) 和 iOS (idb)。

根据 stage.platform 或 steps 中的 platform 字段自动路由。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def run_mobile_case_steps(
    steps: List[Dict[str, Any]],
    *,
    capabilities: Optional[Dict[str, Any]] = None,
    platform: str = "android",
    udid: str = "",
) -> List[Dict[str, Any]]:
    """执行移动端步骤。自动根据 platform 路由到 Android/iOS。"""
    plat = (platform or "android").strip().lower()
    if plat in ("ios", "iphone", "ipad"):
        return _run_ios_steps(steps, udid=udid)
    return _run_android_steps(steps, capabilities=capabilities)


def _run_android_steps(
    steps: List[Dict[str, Any]],
    *,
    capabilities: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Android Appium 执行。"""
    from modules.mobile.mobile_executor import get_mobile_executor

    executor = get_mobile_executor()
    if capabilities:
        executor.connect(capabilities)
    try:
        return executor.execute_steps(steps)
    finally:
        executor.disconnect()


def _run_ios_steps(
    steps: List[Dict[str, Any]],
    *,
    udid: str = "",
) -> List[Dict[str, Any]]:
    """iOS idb 执行：launch → tap/swipe/input → screenshot → assert。"""
    from mobile_engine.device.ios_device import IOSDeviceManager

    mgr = IOSDeviceManager()
    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    if not udid:
        # 尝试自动发现第一个 iOS 设备
        devices = mgr.list_devices()
        if not devices:
            return [{
                "ok": False,
                "error": "未发现 iOS 设备",
                "error_code": "IOS_NO_DEVICE",
                "elapsed_ms": 0,
            }]
        udid = devices[0].udid

    # 预检
    checks = mgr.check_device_readiness(udid)
    if not checks.get("all_passed"):
        return [{
            "ok": False,
            "error": f"iOS 设备预检失败: {checks.get('errors', [])}",
            "error_code": "IOS_PREFLIGHT_FAILED",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }]

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            results.append({"ok": False, "error": f"步骤 {i} 格式无效", "step_index": i})
            continue

        action = str(step.get("action") or "").strip().lower()
        step_t0 = time.perf_counter()
        step_result: Dict[str, Any] = {"step_index": i, "action": action}

        try:
            if action in ("launch", "open_app"):
                bundle_id = step.get("bundle_id") or step.get("input_value") or ""
                ok, msg = mgr.launch_app(udid, bundle_id)
                step_result["ok"] = ok
                step_result["message"] = msg
                if not ok:
                    step_result["error"] = msg
                    step_result["error_code"] = "IOS_LAUNCH_FAILED"

            elif action in ("tap", "click"):
                x = int(step.get("x") or step.get("coordinate_x") or 0)
                y = int(step.get("y") or step.get("coordinate_y") or 0)
                ok = mgr.tap(udid, x, y)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = f"点击 ({x},{y}) 失败"
                    step_result["error_code"] = "IOS_TAP_FAILED"

            elif action in ("long_press",):
                x = int(step.get("x") or 0)
                y = int(step.get("y") or 0)
                duration = int(step.get("duration_ms") or step.get("input_value") or 1000)
                ok = mgr.long_press(udid, x, y, duration_ms=duration)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = "长按失败"
                    step_result["error_code"] = "IOS_LONG_PRESS_FAILED"

            elif action in ("swipe",):
                x1 = int(step.get("x1") or step.get("start_x") or 0)
                y1 = int(step.get("y1") or step.get("start_y") or 0)
                x2 = int(step.get("x2") or step.get("end_x") or 0)
                y2 = int(step.get("y2") or step.get("end_y") or 0)
                duration = int(step.get("duration_ms") or 400)
                ok = mgr.swipe(udid, x1, y1, x2, y2, duration_ms=duration)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = "滑动失败"
                    step_result["error_code"] = "IOS_SWIPE_FAILED"

            elif action in ("input_text", "type", "enter_text"):
                text = step.get("input_value") or step.get("text") or ""
                ok = mgr.input_text(udid, text)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = "输入文本失败"
                    step_result["error_code"] = "IOS_INPUT_FAILED"

            elif action in ("clear_text", "clear"):
                ok = mgr.clear_text(udid)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = "清除文本失败"
                    step_result["error_code"] = "IOS_CLEAR_FAILED"

            elif action in ("press_button", "press_key"):
                button = str(step.get("button") or step.get("input_value") or "HOME").upper()
                ok = mgr.press_button(udid, button)
                step_result["ok"] = ok
                if not ok:
                    step_result["error"] = f"按键 {button} 失败"
                    step_result["error_code"] = "IOS_BUTTON_FAILED"

            elif action in ("screenshot", "capture"):
                output_path = step.get("output_path") or step.get("screenshot_path") or f"ios_step_{i}.png"
                ok = mgr.capture_screenshot(udid, output_path)
                step_result["ok"] = ok
                step_result["screenshot_path"] = output_path
                if not ok:
                    step_result["error"] = "截图失败"
                    step_result["error_code"] = "IOS_SCREENSHOT_FAILED"

            elif action in ("assert", "verify", "check"):
                # 通过 accessibility tree 验证
                label = step.get("selector") or step.get("input_value") or ""
                tree = mgr.get_accessibility_tree(udid)
                if tree and label:
                    from mobile_engine.device.ios_device import _search_tree
                    found = _search_tree(tree, label)
                    step_result["ok"] = found is not None
                    if not found:
                        step_result["error"] = f"未找到元素: {label}"
                        step_result["error_code"] = "IOS_ASSERT_FAILED"
                else:
                    step_result["ok"] = False
                    step_result["error"] = "无法获取 accessibility 树或缺少断言文本"
                    step_result["error_code"] = "IOS_ASSERT_NO_TREE"

            elif action in ("wait", "sleep"):
                seconds = float(step.get("seconds") or step.get("input_value") or 1)
                time.sleep(seconds)
                step_result["ok"] = True

            else:
                step_result["ok"] = False
                step_result["error"] = f"不支持的 iOS 操作: {action}"
                step_result["error_code"] = "IOS_UNSUPPORTED_ACTION"

        except Exception as exc:
            step_result["ok"] = False
            step_result["error"] = str(exc)
            step_result["error_code"] = "IOS_STEP_EXCEPTION"

        step_result["elapsed_ms"] = round((time.perf_counter() - step_t0) * 1000, 1)
        results.append(step_result)

        # 如果某步失败且没有 continue 标记，停止执行
        if not step_result.get("ok") and not step.get("continue_on_failure"):
            break

    return results
