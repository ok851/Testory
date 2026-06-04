# -*- coding: utf-8 -*-
"""跨端联动场景编排（第一迭代：存储与 stub 执行）。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _store_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cross_platform_scenarios.json"


def _load_all() -> List[Dict[str, Any]]:
    p = _store_path()
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_all(items: List[Dict[str, Any]]) -> None:
    p = _store_path()
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_cross_platform_scenarios() -> List[Dict[str, Any]]:
    return _load_all()


def get_cross_platform_scenario(scenario_id: str) -> Optional[Dict[str, Any]]:
    sid = (scenario_id or "").strip()
    for item in _load_all():
        if str(item.get("scenario_id") or "") == sid:
            return item
    return None


def save_cross_platform_scenario(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    CrossPlatformScenario 结构：
    scenario_id, web_case_id, mobile_case_id, sync_points[], title?, note?
    """
    data = dict(payload or {})
    sid = (data.get("scenario_id") or "").strip() or str(uuid.uuid4())[:12]
    data["scenario_id"] = sid
    if not data.get("web_case_id") and not data.get("mobile_case_id"):
        return {"success": False, "error": "至少指定 web_case_id 或 mobile_case_id"}

    items = _load_all()
    replaced = False
    for i, item in enumerate(items):
        if str(item.get("scenario_id") or "") == sid:
            items[i] = data
            replaced = True
            break
    if not replaced:
        items.append(data)
    _save_all(items)
    return {"success": True, "scenario": data}


def delete_cross_platform_scenario(scenario_id: str) -> Dict[str, Any]:
    sid = (scenario_id or "").strip()
    items = [x for x in _load_all() if str(x.get("scenario_id") or "") != sid]
    if len(items) == len(_load_all()):
        return {"success": False, "error": "场景不存在"}
    _save_all(items)
    return {"success": True}


def execute_cross_platform_scenario(scenario_id: str) -> Dict[str, Any]:
    """第二迭代实现双 session 调度；当前返回 stub。"""
    sc = get_cross_platform_scenario(scenario_id)
    if not sc:
        return {"success": False, "error": "联动场景不存在"}
    return {
        "success": False,
        "error": "跨端联动执行将在下一迭代实现（Web Playwright + Appium 交替调度）",
        "scenario": sc,
        "status": "not_implemented",
    }
