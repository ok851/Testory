# -*- coding: utf-8 -*-
"""轻量桌面运行记忆：成功轨迹摘要，供下次同应用任务注入 hint。

不写入 Hermes bundled sync，避免污染官方 skill 树。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _memory_path() -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if base:
        root = Path(base)
    else:
        root = Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "desktop_run_memory.json"


def _load() -> Dict[str, Any]:
    path = _memory_path()
    if not path.is_file():
        return {"version": 1, "apps": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("apps", {})
            return raw
    except Exception:
        pass
    return {"version": 1, "apps": {}}


def _save(data: Dict[str, Any]) -> None:
    path = _memory_path()
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def record_successful_run(
    *,
    app_label: str,
    tools_used: Optional[List[str]] = None,
    phase: str = "",
    user_goal: str = "",
) -> None:
    """任务成功结束时记录简短轨迹。"""
    app = (app_label or "").strip() or "unknown"
    tools = [str(t) for t in (tools_used or []) if t][:24]
    if not tools:
        return
    data = _load()
    apps = data.setdefault("apps", {})
    entry = {
        "updated_at": int(time.time()),
        "phase": (phase or "")[:40],
        "goal": (user_goal or "").strip()[:120],
        "tools": tools,
        "summary": " → ".join(tools[:8]),
    }
    hist = apps.setdefault(app, {"runs": []})
    runs = hist.setdefault("runs", [])
    if not isinstance(runs, list):
        runs = []
    runs.insert(0, entry)
    hist["runs"] = runs[:8]
    hist["last"] = entry
    apps[app] = hist
    _save(data)


def hint_for_app(app_label: str, *, max_chars: int = 400) -> str:
    """返回可注入 system/user 的短 hint；无记忆则空串。"""
    app = (app_label or "").strip()
    if not app:
        return ""
    data = _load()
    apps = data.get("apps") or {}
    # 模糊：子串匹配
    hit = None
    key_l = app.lower()
    for k, v in apps.items():
        if not isinstance(v, dict):
            continue
        if key_l in str(k).lower() or str(k).lower() in key_l:
            hit = v
            break
    if not hit:
        return ""
    last = hit.get("last") if isinstance(hit.get("last"), dict) else None
    if not last:
        runs = hit.get("runs") or []
        last = runs[0] if runs and isinstance(runs[0], dict) else None
    if not last:
        return ""
    summary = str(last.get("summary") or "")[:200]
    goal = str(last.get("goal") or "")[:80]
    text = f"[桌面记忆] 上次在「{app}」成功路径：{summary}"
    if goal:
        text += f"；目标参考：{goal}"
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def apps_from_meta(meta: Optional[Dict[str, Any]]) -> str:
    apps = (meta or {}).get("focused_apps") or []
    if isinstance(apps, list) and apps:
        return str(apps[0])
    return ""
