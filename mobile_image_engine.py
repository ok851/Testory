# -*- coding: utf-8 -*-
"""
移动端 Airtest 风格图像识别（OpenCV 模板匹配，复用 desktop_visual_engine）。
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional, Tuple

from locator_tier_utils import parse_visual_template_value


def _click_offset(selector_value: str) -> Tuple[int, int]:
    if not (selector_value or "").strip().startswith("{"):
        return 0, 0
    try:
        obj = json.loads(selector_value)
        if isinstance(obj, dict):
            return int(obj.get("click_offset_x", 0)), int(obj.get("click_offset_y", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return 0, 0


def _require_cv2():
    try:
        import cv2  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "图像识别需要 opencv-python，请执行: pip install opencv-python numpy"
        ) from exc


def _crop_png_center(png: bytes, cx: int, cy: int, half: int) -> bytes:
    import numpy as np

    _require_cv2()
    import cv2

    arr = np.frombuffer(png, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError("无法解码设备截图")
    h, w = bgr.shape[:2]
    half = max(8, int(half))
    l = max(0, cx - half)
    t = max(0, cy - half)
    r = min(w, cx + half)
    b = min(h, cy + half)
    if r <= l or b <= t:
        raise RuntimeError("裁剪区域无效")
    crop = bgr[t:b, l:r]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError("模板编码失败")
    return buf.tobytes()


def build_visual_template_json(
    screen_png: bytes,
    cx: int,
    cy: int,
    *,
    half_size: int = 40,
    threshold: float = 0.72,
) -> str:
    """围绕点击点裁剪模板，返回 locator_tier 兼容 JSON selector_value。"""
    tpl_png = _crop_png_center(screen_png, cx, cy, half_size)
    b64 = base64.b64encode(tpl_png).decode("ascii")
    payload = {
        "png_b64": b64,
        "threshold": threshold,
        "anchor_x": int(cx),
        "anchor_y": int(cy),
        "click_offset_x": half_size,
        "click_offset_y": half_size,
    }
    return json.dumps(payload, ensure_ascii=False)


def resolve_tap_point_on_screen(
    screen_png: bytes,
    selector_value: str,
    *,
    anchor_x: Optional[int] = None,
    anchor_y: Optional[int] = None,
) -> Tuple[int, int, float]:
    """
    在整屏截图上匹配模板，返回设备坐标 (x, y) 与匹配分。
    """
    from desktop_visual_engine import VisualMatchFailed, locate_template_on_screen

    tpl_bytes, threshold, clip = parse_visual_template_value(selector_value)
    if not tpl_bytes:
        raise RuntimeError("visual_template 缺少有效 PNG 数据")

    ax = anchor_x
    ay = anchor_y
    if ax is None or ay is None:
        if isinstance(clip, dict):
            pass
        raw = (selector_value or "").strip()
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    if obj.get("anchor_x") is not None:
                        ax = int(obj["anchor_x"])
                    if obj.get("anchor_y") is not None:
                        ay = int(obj["anchor_y"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

    if ax is not None and ay is not None:
        import numpy as np

        _require_cv2()
        import cv2

        arr = np.frombuffer(screen_png, dtype=np.uint8)
        scene = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if scene is None:
            raise RuntimeError("无法解码屏幕截图")
        sh, sw = scene.shape[:2]
        half = max(120, min(sw, sh) // 3)
        l = max(0, int(ax) - half)
        t = max(0, int(ay) - half)
        r = min(sw, int(ax) + half)
        b = min(sh, int(ay) + half)
        roi = scene[t:b, l:r]
        ok, buf = cv2.imencode(".png", roi)
        if not ok:
            raise RuntimeError("ROI 编码失败")
        roi_png = buf.tobytes()
        try:
            hit = locate_template_on_screen(
                tpl_bytes,
                roi_png,
                threshold=threshold,
                roi_search=True,
            )
            off_x, off_y = _click_offset(selector_value)
            x = l + hit.left + off_x
            y = t + hit.top + off_y
            return int(x), int(y), float(hit.score)
        except VisualMatchFailed:
            pass

    hit = locate_template_on_screen(tpl_bytes, screen_png, threshold=threshold)
    off_x, off_y = _click_offset(selector_value)
    x = hit.left + off_x
    y = hit.top + off_y
    return int(x), int(y), float(hit.score)


def wait_for_template(
    screen_png: bytes,
    selector_value: str,
    *,
    anchor_x: Optional[int] = None,
    anchor_y: Optional[int] = None,
) -> Tuple[int, int, float]:
    """与 resolve 相同；供 wait_image / assert_image 语义区分。"""
    return resolve_tap_point_on_screen(
        screen_png, selector_value, anchor_x=anchor_x, anchor_y=anchor_y
    )
