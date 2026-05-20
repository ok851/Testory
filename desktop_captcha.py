# -*- coding: utf-8 -*-
"""
Windows 桌面验证码 / 人机校验（执行阶段）。

verify 步骤 input_value / compare_type 取值：
  auto   — 先尝试滑块拖动，再尝试点选类 OCR/视觉
  slider — 在拾取区域内横向拖动（滑块）
  image  — 点选文字类：OCR/本地视觉识别后点击（需 LOCAL_OCR_ENABLE 或 LOCAL_VISION_ENABLE）
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

_CAPTCHA_TYPES = frozenset({"auto", "slider", "image"})


def _grab_region_png(left: int, top: int, width: int, height: int) -> bytes:
    import mss  # type: ignore
    import mss.tools  # type: ignore

    if width < 8 or height < 8:
        raise ValueError("验证码区域过小")
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return mss.tools.to_png(shot.rgb, shot.size)


def _control_screen_rect(ctrl: Any) -> Tuple[int, int, int, int]:
    rect = ctrl.rectangle()
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _drag_slider_rect(left: int, top: int, right: int, bottom: int) -> None:
    import pyautogui  # type: ignore

    pyautogui.FAILSAFE = True
    w = max(right - left, 40)
    h = max(bottom - top, 20)
    cy = top + h // 2
    sx = left + max(14, int(w * 0.06))
    ex = left + int(w * 0.88)
    pyautogui.moveTo(sx, cy, duration=0.15)
    pyautogui.mouseDown()
    steps = 28
    for i in range(1, steps + 1):
        x = sx + (ex - sx) * i // steps
        pyautogui.moveTo(x, cy, duration=0.018)
    pyautogui.mouseUp()
    time.sleep(0.4)


def _parse_click_points_from_vision(text: str) -> List[Tuple[int, int]]:
    if not text:
        return []
    m = re.search(r"\[[\s\S]*?\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    pts: List[Tuple[int, int]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "x" in item and "y" in item:
                pts.append((int(item["x"]), int(item["y"])))
    return pts


def _click_image_captcha_region(left: int, top: int, right: int, bottom: int) -> bool:
    """点选类验证码：视觉/OCR 解析点击坐标（相对区域左上角）。"""
    import pyautogui  # type: ignore

    w = right - left
    h = bottom - top
    png = _grab_region_png(left, top, w, h)

    try:
        from ai_vision_local import ocr_enabled, ocr_region_png, vision_describe, vision_enabled
    except ImportError:
        ocr_enabled = lambda: False  # type: ignore
        vision_enabled = lambda: False  # type: ignore
        ocr_region_png = lambda b: ""  # type: ignore
        vision_describe = lambda b, i: ""  # type: ignore

    if vision_enabled():
        ins = (
            "这是桌面应用验证码区域截图。若为「按顺序点击图片中的文字/图标」类验证码，"
            "仅回复 JSON 数组，元素为 {\"x\":像素,\"y\":像素}，坐标相对图片左上角。"
            "若无法识别或仅为滑块，回复 {\"type\":\"unknown\"}。"
        )
        try:
            out = vision_describe(png, ins)
            if "slider" in (out or "").lower():
                return False
            pts = _parse_click_points_from_vision(out)
            if pts:
                for x, y in pts:
                    pyautogui.click(left + x, top + y)
                    time.sleep(0.35)
                return True
        except Exception:
            pass

    if ocr_enabled():
        text = ocr_region_png(png)
        if text and len(text) >= 2:
            # 无法可靠得到单字坐标时，在区域内均匀尝试几次点击（兜底）
            cy = top + h // 2
            cols = min(4, max(2, len(text.strip())))
            for i in range(cols):
                cx = left + int(w * (i + 1) / (cols + 1))
                pyautogui.click(cx, cy)
                time.sleep(0.25)
            return True

    return False


def run_desktop_verify(
    window: Any,
    selector_type: str,
    selector_value: str,
    desktop_spec: Optional[Dict[str, Any]],
    verify_type: str,
) -> Dict[str, Any]:
    """
    在已附着窗口内处理验证码步骤。
    verify_type: auto | slider | image
    """
    from desktop_locator import resolve_control

    vt = (verify_type or "auto").strip().lower()
    if vt not in _CAPTCHA_TYPES:
        vt = "auto"

    if not selector_value and not (desktop_spec or {}).get("coordinate"):
        raise ValueError("验证码步骤需先拾取验证码区域/控件")

    ctrl = resolve_control(window, selector_type, selector_value, desktop_spec or {})
    left, top, right, bottom = _control_screen_rect(ctrl)
    method = ""

    if vt in ("slider", "auto"):
        try:
            _drag_slider_rect(left, top, right, bottom)
            method = "slider_drag"
            if vt == "slider":
                return {"status": "success", "action": "verify", "method": method}
        except Exception as exc:
            if vt == "slider":
                raise RuntimeError(f"滑块验证码拖动失败: {exc}") from exc

    if vt in ("image", "auto"):
        if _click_image_captcha_region(left, top, right, bottom):
            return {"status": "success", "action": "verify", "method": "image_click"}
        if vt == "image":
            raise RuntimeError(
                "图片点选验证码未能自动处理。请开启 LOCAL_VISION_ENABLE 或 LOCAL_OCR_ENABLE，"
                "或改用手动完成后再继续用例。"
            )

    if vt == "auto" and method == "slider_drag":
        return {"status": "success", "action": "verify", "method": method}

    raise RuntimeError(f"验证码自动处理未完成（类型: {vt}）")
