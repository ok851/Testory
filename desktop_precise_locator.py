# -*- coding: utf-8 -*-
"""桌面截图预览工具（视觉引擎辅助，无 UIA）。"""

from __future__ import annotations

import base64
from typing import Any, Dict

from desktop_runtime import parse_desktop_spec


def capture_rect_preview_b64(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    padding: int = 8,
) -> str:
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
    except ImportError:
        return ""
    l = int(min(left, right)) - padding
    t = int(min(top, bottom)) - padding
    w = max(4, int(max(left, right) - min(left, right)) + 2 * padding)
    h = max(4, int(max(top, bottom) - min(top, bottom)) + 2 * padding)
    try:
        with mss.mss() as sct:
            shot = sct.grab({"left": l, "top": t, "width": w, "height": h})
            return base64.b64encode(mss.tools.to_png(shot.rgb, shot.size)).decode("ascii")
    except Exception:
        return ""


def enrich_desktop_spec_for_precise_run(
    spec: Dict[str, Any],
    locator_candidates: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """兼容旧调用：visual 步骤不再 enrich desktop_spec。"""
    return dict(spec or {})
