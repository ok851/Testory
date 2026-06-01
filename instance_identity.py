# -*- coding: utf-8 -*-
"""实例与机器标识：团队服务器 instance_id、桌面客户端 machine_id。"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


def _data_dir() -> Path:
    raw = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = Path(__file__).resolve().parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _identity_path(name: str) -> Path:
    return _data_dir() / name


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _machine_fingerprint() -> str:
    parts = [
        platform.node(),
        platform.system(),
        platform.machine(),
        platform.processor() or "",
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
    ]
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_machine_id() -> str:
    path = _identity_path("machine_id.json")
    data = _load_json(path)
    mid = (data.get("machine_id") or "").strip()
    if mid:
        return mid
    mid = f"mach_{uuid.uuid4().hex[:24]}"
    _save_json(path, {"machine_id": mid, "fingerprint": _machine_fingerprint()})
    return mid


def get_instance_id() -> str:
    """团队服务器实例 ID（一部署一 ID）。"""
    path = _identity_path("instance_id.json")
    data = _load_json(path)
    iid = (data.get("instance_id") or "").strip()
    if iid:
        return iid
    iid = f"inst_{uuid.uuid4().hex[:24]}"
    _save_json(path, {"instance_id": iid})
    return iid


def get_machine_name() -> str:
    return platform.node() or "unknown"


def get_identity_info() -> Dict[str, Any]:
    return {
        "machine_id": get_machine_id(),
        "machine_name": get_machine_name(),
        "instance_id": get_instance_id(),
        "fingerprint": _machine_fingerprint(),
    }
