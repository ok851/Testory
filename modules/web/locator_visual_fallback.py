"""
视口截图 + OpenCV 模板匹配，用于 locator_candidates.visual_template 降级点击。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np

from modules.web.locator_tier_utils import parse_visual_template_value
from modules.core.optional_cv2 import cv2


def _bgr_from_png_bytes(png: bytes) -> Optional[np.ndarray]:
    if cv2 is None:
        return None
    try:
        arr = np.frombuffer(png, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return im
    except Exception:
        return None


def find_template_center_in_bgr(
    scene_bgr: np.ndarray,
    template_bgr: np.ndarray,
    threshold: float,
) -> Optional[Tuple[int, int, float]]:
    """
    在 scene 中匹配 template，返回场景坐标系下的点击中心 (cx, cy, max_val)。
    """
    if scene_bgr is None or template_bgr is None:
        return None
    sh, sw = scene_bgr.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th < 4 or tw < 4 or th > sh or tw > sw:
        return None
    tpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    scene_gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(scene_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
    _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
    if max_v < threshold:
        return None
    cx = int(max_loc[0] + tw / 2)
    cy = int(max_loc[1] + th / 2)
    return cx, cy, float(max_v)


def apply_clip_rect(sh: int, sw: int, clip: Optional[Dict[str, float]]) -> Tuple[int, int, int, int]:
    """clip 为视口比例 x,y,w,h in [0,1]，返回 x,y,w,h 像素。"""
    if not clip:
        return 0, 0, sw, sh
    try:
        x = int(max(0, float(clip.get("x", 0))) * sw)
        y = int(max(0, float(clip.get("y", 0))) * sh)
        w = int(max(1, float(clip.get("w", 1))) * sw)
        h = int(max(1, float(clip.get("h", 1))) * sh)
    except (TypeError, ValueError):
        return 0, 0, sw, sh
    x = max(0, min(x, sw - 1))
    y = max(0, min(y, sh - 1))
    w = max(1, min(w, sw - x))
    h = max(1, min(h, sh - y))
    return x, y, w, h


def match_template_in_viewport_png(
    viewport_png: bytes,
    selector_value: str,
) -> Optional[Tuple[int, int, float]]:
    """
    viewport_png: 整页视口 PNG（与 Playwright screenshot 一致）。
    返回视口内像素坐标 (cx, cy) 及匹配分数。
    """
    tpl_bytes, threshold, clip = parse_visual_template_value(selector_value)
    if not tpl_bytes:
        return None
    scene = _bgr_from_png_bytes(viewport_png)
    tpl = _bgr_from_png_bytes(tpl_bytes)
    if scene is None or tpl is None:
        return None
    sh, sw = scene.shape[:2]
    x0, y0, cw, ch = apply_clip_rect(sh, sw, clip)
    roi = scene[y0 : y0 + ch, x0 : x0 + cw]
    if roi.size == 0:
        return None
    hit = find_template_center_in_bgr(roi, tpl, threshold=threshold)
    if not hit:
        return None
    cx_roi, cy_roi, mv = hit
    return x0 + cx_roi, y0 + cy_roi, mv


def resize_template_max_side(template_bgr: np.ndarray, max_side: int = 128) -> np.ndarray:
    h, w = template_bgr.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return template_bgr
    scale = max_side / float(m)
    nw = max(4, int(w * scale))
    nh = max(4, int(h * scale))
    return cv2.resize(template_bgr, (nw, nh), interpolation=cv2.INTER_AREA)


def encode_png_bgr(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok or buf is None:
        return b""
    return bytes(buf)


def prepare_template_png_bytes_for_storage(raw_png: bytes) -> bytes:
    """缩小模板，控制 locator_candidates 体积。"""
    max_side = int(os.environ.get("LOCATOR_VISUAL_TEMPLATE_MAX_SIDE", "128") or 128)
    im = _bgr_from_png_bytes(raw_png)
    if im is None:
        return raw_png
    small = resize_template_max_side(im, max_side=max(32, max_side))
    return encode_png_bgr(small) or raw_png
