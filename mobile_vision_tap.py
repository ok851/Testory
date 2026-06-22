"""Mobile Tier4 VLM tap 兜底（Phase 4b，无 scrcpy）。"""
from __future__ import annotations

import base64
from typing import Optional, Tuple

from logger import uat_logger


def ground_mobile_element(
    png_bytes: bytes,
    description: str,
    *,
    viewport_w: int = 1080,
    viewport_h: int = 1920,
) -> Optional[Tuple[int, int]]:
    from ai_vision_grounding import ground_element_from_png, locator_tier_vlm_enabled, shrink_viewport_png
    from mobile_env_config import mobile_screenshot_shrink_factor

    if not locator_tier_vlm_enabled():
        return None
    shrunk, scale = shrink_viewport_png(png_bytes, factor=mobile_screenshot_shrink_factor())
    hit = ground_element_from_png(
        shrunk,
        description,
        viewport_w=max(1, int(viewport_w / scale)),
        viewport_h=max(1, int(viewport_h / scale)),
    )
    if hit and scale > 1.0:
        hit.cx = int(hit.cx * scale)
        hit.cy = int(hit.cy * scale)
    if not hit:
        return None
    return hit.cx, hit.cy


def tap_mobile_by_description(udid: str, description: str) -> Tuple[bool, str]:
    from mobile_agent_client import agent_screenshot, agent_replay_step

    desc = (description or "").strip()
    if not desc:
        return False, "缺少元素描述"
    try:
        snap = agent_screenshot(udid, use_plugin=True)
        b64 = (snap or {}).get("image_b64") or (snap or {}).get("data") or ""
        if not b64:
            return False, "无法获取设备画面"
        png = base64.b64decode(b64)
        w = int((snap or {}).get("width") or 1080)
        h = int((snap or {}).get("height") or 1920)
        pt = ground_mobile_element(png, desc, viewport_w=w, viewport_h=h)
        if not pt:
            return False, f"未在画面上找到：{desc[:80]}"
        step = {"action": "tap", "selector_type": "coord", "selector_value": f"{pt[0]},{pt[1]}"}
        result = agent_replay_step(udid, step, step_index=0)
        if (result or {}).get("success") is False:
            return False, str((result or {}).get("error") or "点击失败")
        uat_logger.info("[MOBILE_VLM_TAP] %r -> (%s,%s)", desc[:60], pt[0], pt[1])
        return True, "已点击"
    except Exception as e:
        uat_logger.warning("mobile vlm tap: %s", e)
        return False, str(e)
