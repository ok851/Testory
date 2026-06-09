# -*- coding: utf-8 -*-
"""桌面窗口位置与尺寸持久化。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _state_path() -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
    return Path(base) / "window.json"


def load_window_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_window_state(
    *,
    width: int,
    height: int,
    x: Optional[int] = None,
    y: Optional[int] = None,
    maximized: bool = False,
) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "width": int(width),
        "height": int(height),
        "x": x,
        "y": y,
        "maximized": bool(maximized),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def resolve_window_geometry(
    *,
    default_width: int = 1440,
    default_height: int = 900,
    min_width: int = 1024,
    min_height: int = 640,
) -> Tuple[int, int, bool, Optional[int], Optional[int]]:
    state = load_window_state()
    w = int(state.get("width") or default_width)
    h = int(state.get("height") or default_height)
    w = max(min_width, w)
    h = max(min_height, h)
    maximized = bool(state.get("maximized"))
    x = state.get("x")
    y = state.get("y")
    return w, h, maximized, (int(x) if x is not None else None), (int(y) if y is not None else None)
