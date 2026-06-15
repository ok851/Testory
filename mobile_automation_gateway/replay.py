# -*- coding: utf-8 -*-
"""经插件 API 回放移动端步骤。"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mobile_automation import prepare_mobile_step
from mobile_automation_gateway import plugin_rpc

_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "static" / "mobile_screenshots"


def _save_screenshot(data: bytes, udid: str, step_index: int) -> str:
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"replay_{udid.replace(':', '_')}_{int(time.time())}_{step_index}.jpg"
    path = _SCREENSHOT_DIR / name
    path.write_bytes(data)
    return f"/static/mobile_screenshots/{name}"


def execute_step(udid: str, step: Dict[str, Any], *, step_index: int = 0) -> Dict[str, Any]:
    udid = (udid or "").strip()
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
