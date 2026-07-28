# -*- coding: utf-8 -*-
"""仓库 → Testory 项目映射（data/ci_repo_map.json）。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()


def _path() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "ci_repo_map.json"


def _load() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"mappings": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mappings": []}
    if not isinstance(data, dict):
        return {"mappings": []}
    if not isinstance(data.get("mappings"), list):
        data["mappings"] = []
    return data


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    with _LOCK:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_repo(repo: str) -> str:
    r = (repo or "").strip().lower()
    r = r.replace("https://", "").replace("http://", "").replace("git@", "")
    r = r.replace("github.com:", "github.com/")
    r = r.replace(".git", "")
    return r.strip("/")


def list_mappings() -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_load().get("mappings") or [])


def upsert_mapping(
    *,
    repo: str,
    project_id: int,
    tenant_id: Optional[int] = None,
    label: str = "",
    default_branch: str = "",
) -> Dict[str, Any]:
    repo_n = _norm_repo(repo)
    if not repo_n:
        raise ValueError("repo 不能为空")
    try:
        pid = int(project_id)
    except (TypeError, ValueError) as e:
        raise ValueError("project_id 无效") from e

    entry = {
        "repo": repo_n,
        "project_id": pid,
        "tenant_id": int(tenant_id) if tenant_id is not None else None,
        "label": (label or "")[:120],
        "default_branch": (default_branch or "")[:120],
    }
    with _LOCK:
        data = _load()
        rows = list(data.get("mappings") or [])
        found = False
        for i, row in enumerate(rows):
            if _norm_repo(str(row.get("repo") or "")) == repo_n:
                rows[i] = {**row, **entry}
                found = True
                break
        if not found:
            rows.append(entry)
        data["mappings"] = rows
        _save(data)
    return entry


def delete_mapping(repo: str) -> bool:
    repo_n = _norm_repo(repo)
    with _LOCK:
        data = _load()
        rows = list(data.get("mappings") or [])

        def _match(stored: str) -> bool:
            s = _norm_repo(stored)
            return s == repo_n or s.endswith(repo_n) or repo_n.endswith(s)

        new_rows = [r for r in rows if not _match(str(r.get("repo") or ""))]
        if len(new_rows) == len(rows):
            return False
        data["mappings"] = new_rows
        _save(data)
    return True


def resolve_project_id(repo: str, branch: str = "") -> Optional[Dict[str, Any]]:
    """按仓库（可选分支前缀匹配 label）解析映射。"""
    repo_n = _norm_repo(repo)
    if not repo_n:
        return None
    rows = list_mappings()
    # 精确 repo 匹配；若多条同 repo，优先 default_branch 匹配
    hits = [r for r in rows if _norm_repo(str(r.get("repo") or "")) == repo_n]
    if not hits:
        # 后缀匹配：acme/app vs github.com/acme/app
        hits = [
            r for r in rows
            if repo_n.endswith(_norm_repo(str(r.get("repo") or "")))
            or _norm_repo(str(r.get("repo") or "")).endswith(repo_n)
        ]
    if not hits:
        return None
    br = (branch or "").strip()
    if br:
        for h in hits:
            db = str(h.get("default_branch") or "").strip()
            if db and db == br:
                return dict(h)
    return dict(hits[0])
