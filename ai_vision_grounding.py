"""
Tier4 VLM 元素定位：视口截图 + 多模态模型 → 视口像素坐标。

环境：
  LOCATOR_TIER_VLM_ENABLE=1  启用 Tier4（面向用户默认开，显式 0 可关）
  LOCATOR_VLM_MODEL          默认继承 LOCAL_VISION_MODEL
  LOCATOR_VLM_SHRINK_FACTOR    截图缩放（>1 缩小），默认 1
  LOCATOR_VLM_CACHE_ENABLE     成功后写回 viewport_coord（由执行器消费）
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from logger import uat_logger
from optional_cv2 import cv2


@dataclass
class GroundResult:
    """VLM 定位结果（视口 CSS 像素）。"""

    cx: int
    cy: int
    fx: float
    fy: float
    prompt: str = ""
    raw: Optional[Dict[str, Any]] = None


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def locator_tier_vlm_enabled() -> bool:
    return _env_bool("LOCATOR_TIER_VLM_ENABLE", True)


def locator_vlm_cache_enabled() -> bool:
    return _env_bool("LOCATOR_VLM_CACHE_ENABLE", True)


def _shrink_factor() -> float:
    raw = (os.environ.get("LOCATOR_VLM_SHRINK_FACTOR") or "1").strip()
    try:
        f = float(raw)
    except ValueError:
        f = 1.0
    return max(1.0, min(f, 4.0))


def _vlm_model() -> str:
    return (
        (os.environ.get("LOCATOR_VLM_MODEL") or os.environ.get("LOCAL_VISION_MODEL") or "llava:7b")
        .strip()
        or "llava:7b"
    )


def shrink_viewport_png(png_bytes: bytes, factor: Optional[float] = None) -> Tuple[bytes, float]:
    """
    缩小截图以降低 token。返回 (png, scale) 其中 scale = 原宽/新宽，用于坐标还原。
    """
    if not png_bytes or cv2 is None:
        return png_bytes, 1.0
    fac = factor if factor is not None else _shrink_factor()
    if fac <= 1.0:
        return png_bytes, 1.0
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return png_bytes, 1.0
        h, w = img.shape[:2]
        nw = max(1, int(w / fac))
        nh = max(1, int(h / fac))
        if nw >= w and nh >= h:
            return png_bytes, 1.0
        small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", small)
        if not ok:
            return png_bytes, 1.0
        scale = w / float(nw)
        return bytes(buf), scale
    except Exception as e:
        uat_logger.debug("shrink_viewport_png: %s", e)
        return png_bytes, 1.0


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text.strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_grounding_response(
    text: str,
    *,
    viewport_w: int,
    viewport_h: int,
    image_scale: float = 1.0,
) -> Optional[GroundResult]:
    """
    解析 VLM 输出。支持：
      {"x": 120, "y": 340}
      {"center": [120, 340]}
      {"fx": 0.25, "fy": 0.75}  0–1 视口比例
      {"point": {"x": 500, "y": 300}}  0–1000 归一化（Qwen-VL 常见）
    """
    data = _extract_json_obj(text)
    if not data:
        return None
    vw = max(1, int(viewport_w))
    vh = max(1, int(viewport_h))
    scale = max(1.0, float(image_scale))

    def _from_norm1000(x: float, y: float) -> Tuple[int, int, float, float]:
        cx = int(clamp01(x / 1000.0) * vw)
        cy = int(clamp01(y / 1000.0) * vh)
        return cx, cy, cx / vw, cy / vh

    def _from_pixels(x: float, y: float) -> Tuple[int, int, float, float]:
        cx = int(max(0, min(vw - 1, round(x * scale))))
        cy = int(max(0, min(vh - 1, round(y * scale))))
        return cx, cy, cx / vw, cy / vh

    def _from_ratio(fx: float, fy: float) -> Tuple[int, int, float, float]:
        fx, fy = clamp01(fx), clamp01(fy)
        cx = int(fx * vw)
        cy = int(fy * vh)
        return cx, cy, fx, fy

    cx = cy = None
    fx = fy = None

    if "fx" in data and "fy" in data:
        try:
            cx, cy, fx, fy = _from_ratio(float(data["fx"]), float(data["fy"]))
        except (TypeError, ValueError):
            pass
    elif "x" in data and "y" in data:
        try:
            xv, yv = float(data["x"]), float(data["y"])
            if xv <= 1.0 and yv <= 1.0:
                cx, cy, fx, fy = _from_ratio(xv, yv)
            elif xv <= vw * 1.05 and yv <= vh * 1.05:
                cx, cy, fx, fy = _from_pixels(xv, yv)
            elif max(xv, yv) <= 1000:
                cx, cy, fx, fy = _from_norm1000(xv, yv)
            else:
                cx, cy, fx, fy = _from_pixels(xv, yv)
        except (TypeError, ValueError):
            pass
    elif isinstance(data.get("center"), (list, tuple)) and len(data["center"]) >= 2:
        try:
            cx, cy, fx, fy = _from_pixels(float(data["center"][0]), float(data["center"][1]))
        except (TypeError, ValueError):
            pass
    elif isinstance(data.get("point"), dict):
        pt = data["point"]
        if "x" in pt and "y" in pt:
            try:
                xv, yv = float(pt["x"]), float(pt["y"])
                if max(xv, yv) > 1.0:
                    cx, cy, fx, fy = _from_norm1000(xv, yv)
                else:
                    cx, cy, fx, fy = _from_ratio(xv, yv)
            except (TypeError, ValueError):
                pass

    if cx is None or cy is None:
        return None
    return GroundResult(cx=cx, cy=cy, fx=fx or (cx / vw), fy=fy or (cy / vh), raw=data)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _grounding_prompt(element_description: str) -> str:
    desc = (element_description or "").strip()
    return (
        "You are a UI element locator. Find the clickable/interactive element described below "
        "in this browser viewport screenshot.\n"
        f"Target element: {desc}\n\n"
        "Reply with ONLY one JSON object (no markdown), using viewport pixel coordinates "
        "relative to the TOP-LEFT of the screenshot image:\n"
        '  {"x": <integer>, "y": <integer>}\n'
        "Alternatively you may use normalized coordinates 0-1:\n"
        '  {"fx": <0-1>, "fy": <0-1>}\n'
        "Or Qwen-style 0-1000 grid:\n"
        '  {"point": {"x": <0-1000>, "y": <0-1000>}}'
    )


def ground_element_from_png(
    png_bytes: bytes,
    element_description: str,
    *,
    viewport_w: int,
    viewport_h: int,
    model: Optional[str] = None,
) -> Optional[GroundResult]:
    """同步：截图 PNG + 自然语言描述 → GroundResult。"""
    if not png_bytes or not (element_description or "").strip():
        return None
    if not locator_tier_vlm_enabled():
        return None

    from ai_vision_local import vision_describe

    shrunk, scale = shrink_viewport_png(png_bytes)
    prompt = _grounding_prompt(element_description)
    try:
        raw = vision_describe(shrunk, prompt, model=model or _vlm_model())
    except ValueError as e:
        uat_logger.warning("[TIER4_VLM] vision call failed: %s", e)
        return None
    hit = parse_grounding_response(
        raw,
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        image_scale=scale,
    )
    if hit:
        hit.prompt = element_description.strip()
        uat_logger.info(
            "[TIER4_VLM] grounded %r -> (%d,%d) fx=%.4f fy=%.4f scale=%.2f",
            element_description[:80],
            hit.cx,
            hit.cy,
            hit.fx,
            hit.fy,
            scale,
        )
    else:
        uat_logger.warning("[TIER4_VLM] could not parse grounding JSON from model")
    return hit


def collect_vlm_prompts(
    locator_candidates_raw: Any,
    *,
    locate_prompt: str = "",
    description: str = "",
) -> List[str]:
    """从 locator_candidates 的 vlm_ground 项与步骤字段收集定位描述。"""
    from locator_tier_utils import parse_vlm_ground_prompt, split_locator_candidates

    prompts: List[str] = []
    seen: set = set()
    for extra in (locate_prompt, description):
        p = (extra or "").strip()
        if p and p not in seen:
            seen.add(p)
            prompts.append(p)
    _, _, _, vlm_list = split_locator_candidates(locator_candidates_raw)
    for item in vlm_list:
        p = parse_vlm_ground_prompt(item.get("selector_value") or "")
        if p and p not in seen:
            seen.add(p)
            prompts.append(p)
    return prompts
