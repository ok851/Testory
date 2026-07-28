# -*- coding: utf-8 -*-
"""本地配置注册中心（R19：Nacos 叙事的轻量替代）。

托管 AgentTeams Spec / Skill 清单的 JSON，不引入 Nacos 运行时依赖。
企业可将同一目录同步到 Nacos 配置中心；本模块保证离线可跑。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "config_registry"
    d.mkdir(parents=True, exist_ok=True)
    (d / "specs").mkdir(exist_ok=True)
    (d / "skills").mkdir(exist_ok=True)
    return d


def publish_spec(spec_id: str, content: Dict[str, Any]) -> Path:
    sid = "".join(c for c in (spec_id or "spec") if c.isalnum() or c in "-_")[:64] or "spec"
    path = _root() / "specs" / f"{sid}.json"
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def get_spec(spec_id: str) -> Optional[Dict[str, Any]]:
    sid = "".join(c for c in (spec_id or "") if c.isalnum() or c in "-_")[:64]
    path = _root() / "specs" / f"{sid}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_specs() -> List[str]:
    return sorted(p.stem for p in (_root() / "specs").glob("*.json"))


def publish_skill_index(entries: List[Dict[str, Any]]) -> Path:
    path = _root() / "skills" / "index.json"
    path.write_text(
        json.dumps({"skills": entries, "note": "local registry; sync to Nacos optionally"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def seed_from_builtin_team_spec() -> Path:
    """将内置 AgentTeams Spec 发布到注册中心。"""
    from ai_modules.agent_teams import load_team_spec

    spec = load_team_spec()
    return publish_spec(str(spec.get("team_id") or "testory-cross-end-qa-team"), spec)


def registry_info() -> Dict[str, Any]:
    return {
        "root": str(_root()),
        "specs": list_specs(),
        "nacos_note": (
            "本目录可被 Nacos 配置托管镜像；本地默认不依赖 Nacos 进程。"
            "勿为演示强行引入中间件堆料。"
        ),
    }
