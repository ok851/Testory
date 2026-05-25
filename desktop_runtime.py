# -*- coding: utf-8 -*-
"""桌面视觉自动化运行时检测（无 pywinauto/UIA 依赖）。"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional


def desktop_runtime_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import cv2  # noqa: F401
        import mss  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def desktop_runtime_unavailable_reason() -> str:
    if sys.platform != "win32":
        return "桌面自动化仅支持 Windows"
    missing = []
    for mod in ("cv2", "mss", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return f"缺少依赖: {', '.join(missing)}（见 requirements.txt）"
    return ""


def parse_desktop_spec(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}
