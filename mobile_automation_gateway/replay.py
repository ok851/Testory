# -*- coding: utf-8 -*-
"""经插件 API 回放移动端步骤（含视觉步骤 ai_tap / assert_vision / wait_vision）。"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mobile_automation import prepare_mobile_step
from mobile_automation_gateway import plugin_rpc

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
    from mobile_env_config import mobile_wait_after_action_ms

    return mobile_wait_after_action_ms()


def _capture_device_png(udid: str) -> Tuple[Optional[bytes], str, int, int]:
    try:
        img, meta = plugin_rpc.take_screenshot(udid)
        w = int((meta or {}).get("width") or 1080)
        h = int((meta or {}).get("height") or 1920)
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
                "description": desc or locate,
            }

        if action == "ai_input":
            if not locate:
                return {"status": "error", "error": "ai_input 缺少输入框描述", "action": action}
            text = str(step.get("input_value") or step.get("text") or "")
            from mobile_vision_tap import tap_mobile_by_description

            ok, msg = tap_mobile_by_description(udid, locate)
            if not ok:
                return {"status": "error", "error": msg, "action": action, "description": desc}
            time.sleep(0.15)
            plugin_rpc.plugin_input(udid, text=text)
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
                "message": "已输入",
                "screenshot": screenshot_url,
                "description": desc or locate,
            }

        png, cap_err, _, _ = _capture_device_png(udid)
        if not png:
            return {"status": "error", "error": cap_err or "无法获取设备画面", "action": action}

        if action == "assert_vision":
            cond = (desc or step.get("input_value") or "").strip()
            if not cond:
                return {"status": "error", "error": "assert_vision 缺少画面描述", "action": action}
            from ai_vision_insight import assert_vision_condition_on_png

            ok, reason = assert_vision_condition_on_png(png, cond)
            if not ok:
                return {
                    "status": "error",
                    "error": reason or "画面确认未通过",
                    "action": action,
                    "description": cond,
                }
            return {
                "status": "success",
                "action": action,
                "message": reason,
                "description": cond,
            }

        if action == "wait_vision":
            cond = (desc or step.get("input_value") or "").strip()
            raw_to = str(step.get("selector_value") or step.get("wait_ms") or "30000").strip()
            try:
                timeout_ms = int(float(raw_to))
            except (TypeError, ValueError):
                timeout_ms = 30000
            if not cond:
                return {"status": "error", "error": "wait_vision 缺少等待描述", "action": action}
            from ai_vision_insight import assert_vision_condition_on_png

            deadline = time.time() + max(1.0, timeout_ms / 1000.0)
            interval = 2.0
            last_reason = ""
            while time.time() < deadline:
                png, cap_err, _, _ = _capture_device_png(udid)
                if not png:
                    last_reason = cap_err or last_reason
                    time.sleep(interval)
                    continue
                ok, reason = assert_vision_condition_on_png(png, cond)
                if ok:
                    return {
                        "status": "success",
                        "action": action,
                        "message": reason or "条件已满足",
                        "description": cond,
                    }
                last_reason = reason or last_reason
                time.sleep(interval)
            return {
                "status": "error",
                "error": last_reason or f"等待超时：{cond[:80]}",
                "action": action,
                "description": cond,
            }

        if action == "extract_vision":
            prompt = (desc or step.get("input_value") or step.get("locate_prompt") or "").strip()
            if not prompt:
                return {"status": "error", "error": "extract_vision 缺少读取描述", "action": action}
            from ai_vision_insight import extract_vision_from_png

            text, err = extract_vision_from_png(png, prompt)
            if not text or err:
                return {
                    "status": "error",
                    "error": err or "未能从画面读取信息",
                    "action": action,
                    "description": prompt,
                }
            return {
                "status": "success",
                "action": action,
                "message": text,
                "data": text,
                "description": prompt,
            }

        return {"status": "error", "error": f"不支持的视觉操作: {action}", "action": action}
    except Exception as exc:
        screenshot_url = ""
        try:
            img, _ = plugin_rpc.take_screenshot(udid)
            screenshot_url = _save_screenshot(img, udid, step_index)
        except Exception:
            pass
        return {
            "status": "error",
            "error": str(exc),
            "action": action,
            "screenshot": screenshot_url,
        }


def execute_step(udid: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    udid = (udid or "").strip()
    raw_action = (step.get("action") or "").strip().lower()
    if raw_action in _VISION_ACTIONS:
        return _execute_vision_step(udid, step, step_index=step_index)

    prepared = prepare_mobile_step(step)
    action = (prepared.get("action") or "").strip().lower()
    stype = (prepared.get("selector_type") or "").strip()
    sval = (prepared.get("selector_value") or "").strip()
    mobile_spec = prepared.get("mobile_spec") if isinstance(prepared.get("mobile_spec"), dict) else {}

    try:
        if action in ("tap", "click"):
            x = y = 0
            if stype == "viewport_coord" and sval:
                import json

                try:
                    coord = json.loads(sval)
                    x, y = int(coord.get("x") or 0), int(coord.get("y") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            elif stype in ("coord", "coordinates") and sval:
                parts = sval.replace(";", ",").split(",")
                if len(parts) >= 2:
                    try:
                        x, y = int(float(parts[0])), int(float(parts[1]))
                    except (TypeError, ValueError):
                        pass
            elif mobile_spec.get("viewport_coord"):
                vc = mobile_spec["viewport_coord"]
                x, y = int(vc.get("x") or 0), int(vc.get("y") or 0)
            plugin_rpc.plugin_tap(udid, selector_type=stype, selector_value=sval, x=x, y=y)
        elif action == "swipe":
            x1 = int(mobile_spec.get("x1") or 0)
            y1 = int(mobile_spec.get("y1") or 0)
            x2 = int(mobile_spec.get("x2") or x1)
            y2 = int(mobile_spec.get("y2") or y1)
            plugin_rpc.plugin_swipe(udid, x1=x1, y1=y1, x2=x2, y2=y2)
        elif action in ("input_text", "input", "type"):
            text = str(prepared.get("input_value") or "")
            plugin_rpc.plugin_input(udid, text=text, selector_type=stype, selector_value=sval)
        elif action in ("press_back", "back"):
            from mobile_adb_control import adb_press_back

            adb_press_back(udid)
        elif action in ("press_home", "home"):
            from mobile_adb_control import adb_press_home

            adb_press_home(udid)
        else:
            return {"status": "error", "error": f"不支持的操作: {action}", "action": action}

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
            "screenshot": screenshot_url,
            "description": prepared.get("description") or "",
        }
    except Exception as exc:
        screenshot_url = ""
        try:
            img, _ = plugin_rpc.take_screenshot(udid)
            screenshot_url = _save_screenshot(img, udid, step_index)
        except Exception:
            pass
        return {
            "status": "error",
            "error": str(exc),
            "action": action,
            "screenshot": screenshot_url,
        }


def run_steps(
    udid: str,
    steps: List[Dict[str, Any]],
    *,
    from_index: int = 0,
) -> Dict[str, Any]:
    udid = (udid or "").strip()
    if not udid:
        return {"success": False, "error": "缺少 udid"}
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    if not ok:
        return {"success": False, "error": msg}
    results: List[Dict[str, Any]] = []
    start = max(0, int(from_index))
    for i, step in enumerate(steps[start:], start=start + 1):
        result = execute_step(udid, step, step_index=i)
        results.append(result)
        if result.get("status") == "error":
            return {"success": False, "results": results, "error": result.get("error"), "failed_at": i}
    return {"success": True, "results": results}
