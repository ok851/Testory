# -*- coding: utf-8 -*-
"""桌面截图预览工具（视觉引擎辅助，无 UIA）。"""

from __future__ import annotations

import base64
import sys
from typing import Any, Dict

from modules.desktop.desktop_runtime import parse_desktop_spec

_DPI_SCALE = 1.0


def _get_dpi_scale() -> float:
    global _DPI_SCALE
    if _DPI_SCALE != 1.0:
        return _DPI_SCALE

    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            hdc = user32.GetDC(0)
            dpi_x = gdi32.GetDeviceCaps(hdc, 88)
            dpi_y = gdi32.GetDeviceCaps(hdc, 90)
            user32.ReleaseDC(0, hdc)

            _DPI_SCALE = dpi_x / 96.0
        except Exception:
            _DPI_SCALE = 1.0
    else:
        _DPI_SCALE = 1.0

    return _DPI_SCALE


def _scale_coords(
    left: int,
    top: int,
    right: int,
    bottom: int,
    scale: float = 1.0,
) -> tuple[int, int, int, int]:
    return (
        int(left * scale),
        int(top * scale),
        int(right * scale),
        int(bottom * scale),
    )


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

    dpi_scale = _get_dpi_scale()

    l = int(min(left, right)) - padding
    t = int(min(top, bottom)) - padding
    w = max(4, int(max(left, right) - min(left, right)) + 2 * padding)
    h = max(4, int(max(top, bottom) - min(top, bottom)) + 2 * padding)

    l_scaled, t_scaled, _, _ = _scale_coords(l, t, l + w, t + h, dpi_scale)
    w_scaled = max(4, int(w * dpi_scale))
    h_scaled = max(4, int(h * dpi_scale))

    try:
        with mss.mss() as sct:
            shot = sct.grab({"left": l_scaled, "top": t_scaled, "width": w_scaled, "height": h_scaled})
            return base64.b64encode(mss.tools.to_png(shot.rgb, shot.size)).decode("ascii")
    except Exception:
        return ""


def get_dpi_scale_factor() -> float:
    return _get_dpi_scale()


def scale_to_physical_coords(x: int, y: int) -> tuple[int, int]:
    scale = _get_dpi_scale()
    return int(x * scale), int(y * scale)


def scale_from_physical_coords(x: int, y: int) -> tuple[int, int]:
    scale = _get_dpi_scale()
    return int(x / scale), int(y / scale)


def enrich_desktop_spec_for_precise_run(
    spec: Dict[str, Any],
    locator_candidates: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """兼容旧调用：visual 步骤不再 enrich desktop_spec。"""
    result = dict(spec or {})
    result["dpi_scale"] = _get_dpi_scale()
    return result
