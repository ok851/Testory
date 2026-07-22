# -*- coding: utf-8 -*-
"""可选 OpenCV：Lite 安装包可不含 cv2，避免启动期硬依赖崩溃。"""
from __future__ import annotations

from typing import Any, Optional

try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover - 取决于安装包是否含 OpenCV
    _cv2 = None

cv2: Any = _cv2
CV2_AVAILABLE = _cv2 is not None


def require_cv2(feature: str = "视觉功能") -> Any:
    """返回 cv2 模块；未安装时抛出可读错误。"""
    if _cv2 is None:
        raise RuntimeError(
            f"{feature}需要 OpenCV 组件。"
            "请在「设置 → 可选组件」安装 OpenCV，或使用含 OpenCV 的完整安装包（-WithOpenCV / -Full）。"
        )
    return _cv2


def get_cv2() -> Optional[Any]:
    return _cv2
