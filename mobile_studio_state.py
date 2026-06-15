# -*- coding: utf-8 -*-
"""移动端测试页会话状态（武装模式等）。"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_arm_state: Dict[str, Any] = {
    "mode": "idle",
    "udid": "",
    "case_id": None,
    "source": "mirror",
}


def get_arm_state() -> Dict[str, Any]:
    with _lock:
        return dict(_arm_state)


def set_arm_state(
    *,
    mode: str = "idle",
    udid: str = "",
    case_id: Optional[int] = None,
    source: str = "mirror",
) -> Dict[str, Any]:
    with _lock:
        _arm_state["mode"] = (mode or "idle").strip().lower()
        _arm_state["udid"] = (udid or "").strip()
        _arm_state["case_id"] = int(case_id) if case_id else None
        _arm_state["source"] = (source or "mirror").strip().lower()
        return dict(_arm_state)


def clear_arm_state() -> Dict[str, Any]:
    return set_arm_state(mode="idle", udid="", case_id=None, source="mirror")
