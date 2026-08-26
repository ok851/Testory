# -*- coding: utf-8
"""
Windows 桌面验证码 / 人机校验（执行阶段）。

verify 步骤 input_value / compare_type 取值：
  auto   — 先尝试滑块拖动，再尝试点选类 OCR/视觉
  slider — 在拾取区域内横向拖动（滑块）
  image  — 点选文字类：OCR/本地视觉识别后点击（需 LOCAL_OCR_ENABLE 或 LOCAL_VISION_ENABLE）
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from modules.web.captcha_engine import (
    build_human_drag_path,
    captcha_solve_attempts,
    captcha_solve_retry_delay,
    detect_captcha_type,
    emit_captcha_status,
    prepare_recognition_png,
    solve_captcha,
    solve_curve_offset,
    solve_slider_gap,
)
from modules.web.captcha_recovery import CaptchaManualRequiredError, save_captcha_failure_screenshot
from modules.core.logger import uat_logger

_CAPTCHA_TYPES = frozenset({"auto", "slider", "image"})


def _grab_region_png(left: int, top: int, width: int, height: int) -> bytes:
    import mss  # type: ignore
    import mss.tools  # type: ignore

    if width < 8 or height < 8:
        raise ValueError("验证码区域过小")
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return mss.tools.to_png(shot.rgb, shot.size)


def _region_changed(before: bytes, after: bytes, threshold: float = 0.02) -> bool:
    if not before or not after or len(before) != len(after):
        return True
    try:
        import numpy as np

        a = np.frombuffer(before, dtype=np.uint8)
        b = np.frombuffer(after, dtype=np.uint8)
        diff = np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))
        return diff > threshold * 255
    except Exception:
        return before != after


def _prepare_desktop_png(raw_png: bytes) -> Tuple[bytes, float]:
    """Desktop 截图缩放到 50% 左右再识别，坐标通过 multiplier 映射回屏幕。"""
    return prepare_recognition_png(raw_png, for_desktop=True)


def _drag_slider_rect(left: int, top: int, right: int, bottom: int) -> bool:
    import pyautogui  # type: ignore

    pyautogui.FAILSAFE = True
    w = max(right - left, 40)
    h = max(bottom - top, 20)
    cy = top + h // 2
    sx = left + max(14, int(w * 0.06))
    track_end = left + int(w * 0.92)

    raw_png = _grab_region_png(left, top, w, h)
    png, mult = _prepare_desktop_png(raw_png)
    instruction = ""
    ctype = detect_captcha_type(instruction)
    distance: Optional[int] = None

    emit_captcha_status("正在识别桌面滑块验证码…")
    if ctype == "curve":
        distance = solve_curve_offset(png)
        if distance is not None:
            distance = int(distance * mult)
    if distance is None:
        dist_raw = solve_slider_gap(png)
        if dist_raw is not None:
            distance = int(dist_raw * mult)
    if distance is None or distance <= 0:
        result = solve_captcha(
            png, captcha_type=ctype or "slider", instruction=instruction, coord_multiplier=mult
        )
        distance = result.distance

    if distance is None or distance <= 0:
        uat_logger.warning("桌面滑块：无法计算缺口距离，跳过拖动")
        return False

    ex = min(sx + int(distance), track_end)

    path = build_human_drag_path(sx, cy, ex, cy)
    pyautogui.moveTo(path[0][0], path[0][1], duration=0.12)
    pyautogui.mouseDown()
    for x, y in path[1:]:
        pyautogui.moveTo(x, y, duration=0.012)
    pyautogui.mouseUp()
    time.sleep(0.45)
    return True


def _click_image_captcha_region(left: int, top: int, right: int, bottom: int) -> bool:
    import pyautogui  # type: ignore

    w = right - left
    h = bottom - top
    before = _grab_region_png(left, top, w, h)
    png, mult = _prepare_desktop_png(before)
    emit_captcha_status("正在识别桌面点选验证码…")
    result = solve_captcha(png, captcha_type="click_text", instruction="", coord_multiplier=mult)

    if result.points:
        for x, y in result.points:
            pyautogui.click(left + x, top + y)
            time.sleep(0.35)
        after = _grab_region_png(left, top, w, h)
        return _region_changed(before, after)
    return False


def _desktop_captcha_with_retry(
    left: int, top: int, right: int, bottom: int, vt: str
) -> str:
    """Desktop 验证码：同题多次求解，不刷新换题。"""
    attempts = captcha_solve_attempts()
    delay = captcha_solve_retry_delay()
    last_png = b""
    for i in range(attempts):
        emit_captcha_status(f"桌面验证码处理中（{i + 1}/{attempts}）…")
        if vt in ("slider", "auto"):
            try:
                _drag_slider_rect(left, top, right, bottom)
                return "slider_drag"
            except Exception as exc:
                if vt == "slider" and i >= attempts - 1:
                    raise RuntimeError(f"滑块验证码拖动失败: {exc}") from exc
        if vt in ("image", "auto"):
            if _click_image_captcha_region(left, top, right, bottom):
                return "image_click"
        try:
            w, h = right - left, bottom - top
            last_png = _grab_region_png(left, top, w, h)
        except Exception:
            pass
        if i < attempts - 1:
            time.sleep(delay)
    shot = save_captcha_failure_screenshot(last_png, prefix="desktop_captcha_fail")
    msg = "自动验证失败，请手动完成验证码后继续。桌面会话已保留。"
    if shot:
        msg += f" 失败截图：{shot}"
    raise CaptchaManualRequiredError(msg, screenshot_path=shot)


def run_desktop_verify(
    window: Any,
    selector_type: str,
    selector_value: str,
    desktop_spec: Optional[Dict[str, Any]],
    verify_type: str,
    app: Any = None,
) -> Dict[str, Any]:
    del window, app, desktop_spec
    st = (selector_type or "").strip().lower()
    if st == "visual":
        raise RuntimeError("visual verify 请通过 desktop_automation 执行")
    raise RuntimeError("UIA verify 已移除，请使用 visual 框选录制")


def run_desktop_verify_at_point(
    x: int, y: int, verify_type: str, *, region_half: int = 120
) -> Dict[str, Any]:
    vt = (verify_type or "auto").strip().lower()
    if vt not in _CAPTCHA_TYPES:
        vt = "auto"
    half = max(40, int(region_half))
    left = int(x) - half
    top = int(y) - half
    right = int(x) + half
    bottom = int(y) + half

    method = _desktop_captcha_with_retry(left, top, right, bottom, vt)
    return {"status": "success", "action": "verify", "method": method}
